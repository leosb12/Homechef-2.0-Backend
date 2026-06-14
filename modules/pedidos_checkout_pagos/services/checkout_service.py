from decimal import Decimal
from secrets import randbelow
from uuid import UUID

from modules.confianza_administracion_seguridad.services import NotificationService
from django.db import transaction

from modules.delivery_logistica.services.base_delivery_service import BaseDeliveryService
from modules.delivery_logistica.services.osm_routing_service import OSMRoutingService
from modules.gestion_cocinero.models import ChefAvailability
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import (
    Cart,
    Order,
    OrderAddress,
    OrderItem,
    OrderPayment,
    OrderPaymentEvent,
    OrderStatusHistory,
    OrderTimelineEvent,
    PickupConfirmation,
)
from modules.pedidos_checkout_pagos.services.cart_service import CartServiceError
from modules.pedidos_checkout_pagos.services.order_coingate_service import OrderCoinGateService, OrderCoinGateServiceError
from modules.pedidos_checkout_pagos.services.order_stripe_service import OrderStripeService, OrderStripeServiceError
from modules.pedidos_checkout_pagos.services.qr_payment_service import QRPaymentService
from modules.pedidos_checkout_pagos.services.stock_service import DishStockService, StockValidationError


class CheckoutServiceError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class CheckoutService:
    DELIVERY_FEE = Decimal("7.00")
    PICKUP_FEE = Decimal("0.00")
    SERVICE_FEE = Decimal("0.00")
    DISCOUNT_TOTAL = Decimal("0.00")
    DEFAULT_PREPARATION_MINUTES = 25
    DEFAULT_DELIVERY_MINUTES = 18
    PICKUP_INSTRUCTIONS = "Presenta este codigo al cocinero para retirar tu pedido."

    def __init__(self):
        self.stock_service = DishStockService()
        self.qr_payment_service = QRPaymentService()
        self.coingate_service = OrderCoinGateService()
        self.stripe_service = OrderStripeService()
        self.delivery_service = BaseDeliveryService()
        self.notification_service = NotificationService()
        self.routing_service = OSMRoutingService()

    def preview(self, user_id: str, payload: dict):
        client = self._require_client(user_id)
        cart = self._get_active_cart(client, payload["cart_id"])
        fulfillment_type = payload["fulfillment_type"]
        payment_method = payload.get("payment_method") or Order.PaymentMethod.CASH
        notes = str(payload.get("notes", "")).strip()
        address = payload.get("address") or {}

        if payment_method not in {Order.PaymentMethod.CASH, Order.PaymentMethod.STRIPE_TEST, Order.PaymentMethod.QR_SIMULATED, Order.PaymentMethod.BITCOIN_COINGATE}:
            raise CheckoutServiceError(
                "En esta fase el checkout admite efectivo, Stripe test, QR simulado o Bitcoin CoinGate.",
                "payment_method_not_supported",
            )

        validation_messages = []
        items_payload = []
        subtotal = Decimal("0")
        for cart_item in cart.items.select_related("dish").all().order_by("created_at"):
            try:
                snapshot = self.stock_service.validate_dish_request(cart_item.dish_id, cart_item.quantity, fulfillment_type)
                item_messages = []
                validated = True
                available_portions = snapshot.available_portions
            except StockValidationError as exc:
                validated = False
                available_portions = exc.details.get("available_portions", 0)
                item_messages = [self._stock_error_message(exc)]
                validation_messages.extend(item_messages)

            unit_price = Decimal(str(cart_item.dish.price))
            item_subtotal = unit_price * cart_item.quantity
            subtotal += item_subtotal
            items_payload.append(
                {
                    "dish_id": cart_item.dish_id,
                    "dish_name": cart_item.dish.name,
                    "chef_id": str(cart.chef.supabase_user_id),
                    "chef_name": self._chef_name(cart.chef),
                    "quantity": cart_item.quantity,
                    "unit_price": float(unit_price),
                    "subtotal": float(item_subtotal),
                    "available_portions": available_portions,
                    "validated": validated,
                    "validation_messages": item_messages,
                }
            )

        self._validate_address(fulfillment_type, address)

        delivery_fee = self.DELIVERY_FEE if fulfillment_type == Order.FulfillmentType.DELIVERY else self.PICKUP_FEE
        total = subtotal + delivery_fee + self.SERVICE_FEE - self.DISCOUNT_TOTAL

        return {
            "cart_id": cart.id,
            "currency": cart.currency,
            "fulfillment_type": fulfillment_type,
            "payment_method": payment_method,
            "items": items_payload,
            "pricing": {
                "subtotal": float(subtotal),
                "delivery_fee": float(delivery_fee),
                "service_fee": float(self.SERVICE_FEE),
                "discount_total": float(self.DISCOUNT_TOTAL),
                "total": float(total),
            },
            "stock_validation": {
                "ok": len(validation_messages) == 0,
                "messages": validation_messages,
            },
            "estimated_times": {
                "preparation_minutes": self.DEFAULT_PREPARATION_MINUTES,
                "delivery_minutes": self.DEFAULT_DELIVERY_MINUTES if fulfillment_type == Order.FulfillmentType.DELIVERY else None,
                "pickup_ready_minutes": self.DEFAULT_PREPARATION_MINUTES if fulfillment_type == Order.FulfillmentType.PICKUP else None,
            },
            "payment_options": [
                Order.PaymentMethod.CASH,
                Order.PaymentMethod.STRIPE_TEST,
                Order.PaymentMethod.QR_SIMULATED,
                Order.PaymentMethod.BITCOIN_COINGATE,
            ],
            "notes": notes,
        }

    def preview_route(self, user_id: str, payload: dict):
        client = self._require_client(user_id)
        cart = self._get_active_cart(client, payload["cart_id"])
        chef_location = self._chef_location(cart.chef)
        if chef_location["lat"] is None or chef_location["lng"] is None:
            raise CheckoutServiceError(
                "El cocinero no tiene una ubicacion configurada para calcular la ruta.",
                "chef_location_missing",
            )

        destination = {
            "lat": payload["latitude"],
            "lng": payload["longitude"],
        }
        route = self.routing_service.build_walking_route(chef_location, destination)
        return {
            "chef": chef_location,
            "destination": destination,
            "route": route,
            "navigation": {
                "mode": "walking",
                "provider": route.get("provider", ""),
                "uses_fallback": route.get("provider") != "OSM_OSRM",
            },
        }

    @transaction.atomic
    def confirm(self, user_id: str, payload: dict):
        client = self._require_client(user_id)
        cart = self._get_active_cart(client, payload["cart_id"], lock=True)
        preview = self.preview(user_id, payload)
        if not preview["stock_validation"]["ok"]:
            raise CheckoutServiceError(
                "No se puede confirmar el pedido por disponibilidad insuficiente.",
                "stock_validation_failed",
                {"messages": preview["stock_validation"]["messages"]},
            )

        expected_total = payload.get("expected_total")
        current_total = Decimal(str(preview["pricing"]["total"]))
        if expected_total is not None and Decimal(str(expected_total)) != current_total:
            raise CheckoutServiceError(
                "El costo final cambio. Revisa el resumen antes de confirmar.",
                "pricing_changed",
                {"expected_total": float(expected_total), "current_total": float(current_total)},
            )

        payment_method = payload.get("payment_method") or Order.PaymentMethod.CASH
        order_status = Order.Status.AWAITING_CHEF_CONFIRMATION if payment_method == Order.PaymentMethod.CASH else Order.Status.PAYMENT_VALIDATING
        order = Order.objects.create(
            client=client,
            chef=cart.chef,
            source_cart=cart,
            status=order_status,
            fulfillment_type=payload["fulfillment_type"],
            payment_method=payment_method,
            currency=cart.currency,
            subtotal=Decimal(str(preview["pricing"]["subtotal"])),
            delivery_fee=Decimal(str(preview["pricing"]["delivery_fee"])),
            service_fee=Decimal(str(preview["pricing"]["service_fee"])),
            discount_total=Decimal(str(preview["pricing"]["discount_total"])),
            total=current_total,
            notes=str(payload.get("notes", "")).strip(),
        )

        order_items = []
        for cart_item in cart.items.select_related("dish").all().order_by("created_at"):
            dish = cart_item.dish
            order_items.append(
                OrderItem(
                    order=order,
                    dish=dish,
                    quantity=cart_item.quantity,
                    unit_price=Decimal(str(dish.price)),
                    subtotal=Decimal(str(dish.price)) * cart_item.quantity,
                    dish_name_snapshot=dish.name,
                    dish_description_snapshot=dish.description or "",
                    dish_image_url_snapshot=dish.image_url or "",
                )
            )
        OrderItem.objects.bulk_create(order_items)

        if payload["fulfillment_type"] == Order.FulfillmentType.DELIVERY:
            address = payload.get("address") or {}
            OrderAddress.objects.create(
                order=order,
                label=str(address.get("label", "")).strip(),
                contact_name=str(address.get("contact_name", "")).strip(),
                contact_phone=str(address.get("contact_phone", "")).strip(),
                line_1=str(address.get("line_1", "")).strip(),
                reference=str(address.get("reference", "")).strip(),
                latitude=address.get("latitude"),
                longitude=address.get("longitude"),
            )
            self.delivery_service.ensure_assignment_for_order(order)
        else:
            PickupConfirmation.objects.create(
                order=order,
                pickup_code=self._generate_pickup_code(),
                pickup_instructions=self.PICKUP_INSTRUCTIONS,
                pickup_schedule_note=f"Retiro estimado en {self.DEFAULT_PREPARATION_MINUTES} minutos.",
            )

        payment = OrderPayment.objects.create(
            order=order,
            method=payment_method,
            status=OrderPayment.Status.PENDING,
            currency=order.currency,
            amount=order.total,
            provider="",
            payment_url="",
            external_reference="",
        )

        self.stock_service.reserve_order_stock(order)
        cart.status = Cart.Status.CONVERTED
        cart.converted_to_order = order
        cart.save(update_fields=["status", "converted_to_order", "updated_at"])

        self._log_status(order, "", Order.Status.CHECKOUT_PENDING, "CLIENTE", user_id, "Checkout confirmado")
        self._log_status(order, Order.Status.CHECKOUT_PENDING, Order.Status.PENDING_PAYMENT, "CLIENTE", user_id, "Pedido materializado")
        if payment_method == Order.PaymentMethod.CASH:
            self._log_status(order, Order.Status.PENDING_PAYMENT, Order.Status.AWAITING_CHEF_CONFIRMATION, "SISTEMA", user_id, "Pedido cash pendiente de cobranza")
            self._log_event(order, "PAYMENT_CREATED", "Pago en efectivo creado", "SISTEMA", user_id, {"payment_id": payment.id, "method": payment.method})
            self._log_payment_event(payment, "PAYMENT_CREATED", "Pago en efectivo creado", "SISTEMA", user_id, {"order_id": order.id, "method": payment.method})
        elif payment_method == Order.PaymentMethod.STRIPE_TEST:
            self._log_status(order, Order.Status.PENDING_PAYMENT, Order.Status.PAYMENT_VALIDATING, "SISTEMA", user_id, "Pedido Stripe test pendiente de validacion")
            try:
                payment = self.stripe_service.create_payment(
                    order=order,
                    payment=payment,
                    success_redirect_to=str(payload.get("success_redirect_to", "")).strip(),
                    cancel_redirect_to=str(payload.get("cancel_redirect_to", "")).strip(),
                )
            except OrderStripeServiceError as exc:
                raise CheckoutServiceError(exc.message, exc.code, exc.details)
            self._log_event(order, "PAYMENT_CREATED", "Pago Stripe test creado", "SISTEMA", user_id, {"payment_id": payment.id, "method": payment.method, "external_reference": payment.external_reference})
            self._log_payment_event(payment, "PAYMENT_CREATED", "Pago Stripe test creado", "SISTEMA", user_id, {"order_id": order.id, "method": payment.method, "external_reference": payment.external_reference})
        elif payment_method == Order.PaymentMethod.QR_SIMULATED:
            self._log_status(order, Order.Status.PENDING_PAYMENT, Order.Status.PAYMENT_VALIDATING, "SISTEMA", user_id, "Pedido QR pendiente de validacion")
            session = self.qr_payment_service.create_session(order, payment, user_id)
            payment.refresh_from_db()
            self._log_event(order, "PAYMENT_CREATED", "Pago QR simulado creado", "SISTEMA", user_id, {"payment_id": payment.id, "method": payment.method, "session_code": session.session_code})
            self._log_payment_event(payment, "PAYMENT_CREATED", "Pago QR simulado creado", "SISTEMA", user_id, {"order_id": order.id, "method": payment.method, "session_code": session.session_code})
        else:
            self._log_status(order, Order.Status.PENDING_PAYMENT, Order.Status.PAYMENT_VALIDATING, "SISTEMA", user_id, "Pedido Bitcoin CoinGate pendiente de validacion")
            try:
                payment = self.coingate_service.create_payment(
                    order=order,
                    payment=payment,
                    success_redirect_to=str(payload.get("success_redirect_to", "")).strip(),
                    cancel_redirect_to=str(payload.get("cancel_redirect_to", "")).strip(),
                )
            except OrderCoinGateServiceError as exc:
                raise CheckoutServiceError(exc.message, exc.code, exc.details)
            self._log_event(order, "PAYMENT_CREATED", "Pago Bitcoin CoinGate creado", "SISTEMA", user_id, {"payment_id": payment.id, "method": payment.method, "external_reference": payment.external_reference})
            self._log_payment_event(payment, "PAYMENT_CREATED", "Pago Bitcoin CoinGate creado", "SISTEMA", user_id, {"order_id": order.id, "method": payment.method, "external_reference": payment.external_reference})
        self._log_event(order, "ORDER_CREATED", "Pedido creado", "CLIENTE", user_id, {"cart_id": cart.id})
        self.delivery_service.sync_assignment_for_order_status(
            order,
            actor_role="CLIENTE",
            actor_id=user_id,
            notes="Entrega base creada para pedido delivery",
        )
        self.notification_service.notify_order_created(order)

        return {
            "order_id": order.id,
            "status": order.status,
            "payment": {
                "payment_id": payment.id,
                "method": payment.method,
                "status": payment.status,
                "payment_url": payment.payment_url,
                "external_reference": payment.external_reference,
                "expires_at": payment.expires_at.isoformat() if payment.expires_at else None,
            },
            "tracking_url": f"/api/v1/orders/my-orders/{order.id}/tracking/",
            "order_detail_url": f"/api/v1/orders/my-orders/{order.id}/",
        }

    def _require_client(self, user_id: str):
        parsed = self._parse_uuid(user_id)
        client = UserProfile.objects.filter(supabase_user_id=parsed, role=UserProfile.ROLE_CLIENT).first() if parsed else None
        if not client:
            raise CheckoutServiceError("Perfil de cliente no encontrado.", "client_not_found")
        return client

    def _get_active_cart(self, client: UserProfile, cart_id: str, lock: bool = False):
        queryset = Cart.objects.filter(id=str(cart_id), client=client, status=Cart.Status.ACTIVE).select_related("chef")
        if lock:
            queryset = queryset.select_for_update()
        cart = queryset.first()
        if not cart:
            raise CheckoutServiceError("Carrito no encontrado o ya no esta activo.", "cart_not_found")
        if not cart.items.exists():
            raise CheckoutServiceError("El carrito no tiene items para checkout.", "empty_cart")
        if not self._chef_is_operational(cart.chef):
            raise CheckoutServiceError("El cocinero no esta operativo para recibir pedidos.", "chef_unavailable")
        return cart

    def _validate_address(self, fulfillment_type: str, address: dict):
        if fulfillment_type == Order.FulfillmentType.PICKUP:
            return
        line_1 = str(address.get("line_1", "")).strip()
        contact_name = str(address.get("contact_name", "")).strip()
        contact_phone = str(address.get("contact_phone", "")).strip()
        if not line_1:
            raise CheckoutServiceError("Debes registrar una direccion para delivery.", "address_required")
        if not contact_name:
            raise CheckoutServiceError("Debes registrar un contacto para delivery.", "contact_name_required")
        if not contact_phone:
            raise CheckoutServiceError("Debes registrar un telefono de contacto.", "contact_phone_required")

    def _stock_error_message(self, exc: StockValidationError):
        if exc.code == "chef_unavailable":
            return "El cocinero no esta disponible temporalmente."
        if exc.code == "modality_not_allowed":
            return "La modalidad seleccionada no esta disponible."
        if exc.code == "insufficient_portions":
            return "No hay porciones suficientes para uno de los platos."
        return "Uno de los platos ya no esta disponible."

    def _chef_name(self, chef: UserProfile):
        try:
            profile = chef.chef_profile
        except Exception:
            profile = None
        if profile and getattr(profile, "business_name", ""):
            return profile.business_name
        full_name = f"{chef.first_name} {chef.last_name}".strip()
        return full_name or "Cocinero HomeChef"

    def _chef_location(self, chef: UserProfile):
        try:
            profile = chef.chef_profile
        except Exception:
            profile = None
        return {
            "lat": getattr(profile, "location_latitude", None),
            "lng": getattr(profile, "location_longitude", None),
            "address": getattr(profile, "location_address", "") if profile else "",
        }

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _generate_pickup_code(self):
        while True:
            code = f"{randbelow(1000000):06d}"
            if not PickupConfirmation.objects.filter(pickup_code=code).exists():
                return code

    def _log_status(self, order: Order, from_status: str, to_status: str, actor_role: str, actor_id: str, notes: str):
        OrderStatusHistory.objects.create(
            order=order,
            from_status=from_status,
            to_status=to_status,
            actor_role=actor_role,
            actor_id=str(actor_id),
            notes=notes,
        )

    def _log_event(self, order: Order, event_code: str, event_label: str, actor_role: str, actor_id: str, metadata: dict):
        OrderTimelineEvent.objects.create(
            order=order,
            event_code=event_code,
            event_label=event_label,
            actor_role=actor_role,
            actor_id=str(actor_id),
            metadata=metadata,
        )

    def _log_payment_event(self, payment: OrderPayment, event_code: str, event_label: str, actor_role: str, actor_id: str, metadata: dict):
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code=event_code,
            event_label=event_label,
            actor_role=actor_role,
            actor_id=str(actor_id),
            metadata=metadata,
        )

    def _chef_is_operational(self, chef: UserProfile):
        availability = ChefAvailability.objects.filter(chef=chef).first()
        if not availability:
            return False
        return bool(availability.is_active and (availability.accept_delivery or availability.accept_pickup))
