from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import UUID

import stripe
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from modules.confianza_administracion_seguridad.services import NotificationService
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import Order, OrderPayment, OrderPaymentEvent, OrderStatusHistory, OrderTimelineEvent
from modules.pedidos_checkout_pagos.services.stock_service import DishStockService


class OrderStripeServiceError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class OrderStripeService:
    PROVIDER = "STRIPE_SANDBOX"

    def __init__(self):
        self.stock_service = DishStockService()
        self.notification_service = NotificationService()

    def create_payment(self, *, order: Order, payment: OrderPayment, success_redirect_to: str = "", cancel_redirect_to: str = ""):
        if not settings.STRIPE_SECRET_KEY:
            raise OrderStripeServiceError("Stripe test no esta configurado en el backend.", "provider_not_configured")

        stripe.api_key = settings.STRIPE_SECRET_KEY
        success_url = str(success_redirect_to or settings.ORDER_STRIPE_SUCCESS_URL).strip()
        cancel_url = str(cancel_redirect_to or settings.ORDER_STRIPE_CANCEL_URL).strip()
        metadata = {
            "payment_id": str(payment.id),
            "order_id": str(order.id),
            "client_id": str(order.client_id),
            "chef_id": str(order.chef_id),
            "provider": self.PROVIDER,
        }
        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                payment_method_types=["card"],
                success_url=with_query_param(success_url, "session_id", "{CHECKOUT_SESSION_ID}"),
                cancel_url=with_query_param(cancel_url, "session_id", "{CHECKOUT_SESSION_ID}"),
                line_items=[
                    {
                        "price_data": {
                            "currency": payment.currency.lower(),
                            "unit_amount": int((Decimal(payment.amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
                            "product_data": {
                                "name": f"Pedido HomeChef {order.id}",
                                "description": f"Pago del pedido {order.id} en modo test",
                            },
                        },
                        "quantity": 1,
                    }
                ],
                metadata=metadata,
                payment_intent_data={"metadata": metadata},
            )
        except stripe.error.StripeError as exc:
            raise OrderStripeServiceError(
                "Stripe test rechazo la creacion del checkout.",
                "provider_rejected",
                {"error": str(exc)},
            )

        payment.provider = self.PROVIDER
        payment.status = OrderPayment.Status.PENDING
        payment.payment_url = session.url or ""
        payment.external_reference = session.id
        payment.provider_payload = {
            "id": session.id,
            "url": session.url,
            "mode": session.mode,
            "payment_status": session.payment_status,
            "metadata": metadata,
            "success_url": with_query_param(success_url, "session_id", "{CHECKOUT_SESSION_ID}"),
            "cancel_url": with_query_param(cancel_url, "session_id", "{CHECKOUT_SESSION_ID}"),
        }
        payment.save(update_fields=["provider", "status", "payment_url", "external_reference", "provider_payload", "updated_at"])

        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="STRIPE_PAYMENT_CREATED",
            event_label="Checkout Stripe test creado",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"external_reference": session.id},
        )
        OrderTimelineEvent.objects.create(
            order=order,
            event_code="STRIPE_PAYMENT_CREATED",
            event_label="Checkout Stripe test creado",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"payment_id": payment.id, "external_reference": session.id},
        )
        return payment

    @transaction.atomic
    def confirm_checkout_return(self, user_id: str, *, provider: str = "", stripe_session_id: str = ""):
        client = self._require_client(user_id)
        payment = self._find_client_payment(client, stripe_session_id)
        if not payment:
            raise OrderStripeServiceError("No se encontro un pago Stripe para este cliente.", "payment_not_found")
        return self.confirm_payment_state(payment, stripe_session_id=stripe_session_id, trust_provider_return=False)

    @transaction.atomic
    def confirm_payment_state(self, payment: OrderPayment, *, stripe_session_id: str = "", trust_provider_return: bool = False):
        if trust_provider_return and settings.DEBUG:
            self._approve_payment(payment, {"sandbox_return": True, "stripe_session_id": stripe_session_id})
            return self._return_payload(payment, handled=True)

        if not payment.external_reference or not settings.STRIPE_SECRET_KEY:
            raise OrderStripeServiceError("Stripe test no esta configurado correctamente.", "provider_not_configured")

        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            session = stripe.checkout.Session.retrieve(payment.external_reference)
        except stripe.error.StripeError as exc:
            raise OrderStripeServiceError(
                "No se pudo consultar el checkout Stripe test.",
                "provider_request_failed",
                {"error": str(exc)},
            )

        response = self._response_dict(session)
        payment_status = self._response_value(session, "payment_status")
        checkout_status = self._response_value(session, "status")
        if payment_status == "paid":
            self._approve_payment(payment, {"stripe_session": response})
        elif checkout_status == "expired":
            self._expire_payment(payment, {"stripe_session": response})
        elif checkout_status == "complete" and payment_status != "paid":
            self._fail_payment(payment, {"stripe_session": response}, "Stripe reporto checkout completo sin pago confirmado")
        elif payment_status in {"unpaid", "no_payment_required"} or checkout_status == "open":
            self._mark_pending(payment, {"stripe_session": response})
        else:
            self._fail_payment(payment, {"stripe_session": response}, f"Stripe {checkout_status or payment_status or 'failed'}")
        return self._return_payload(payment, handled=True)

    def _approve_payment(self, payment: OrderPayment, provider_response: dict):
        if payment.status == OrderPayment.Status.CONFIRMED:
            return
        now = timezone.now()
        payment.status = OrderPayment.Status.CONFIRMED
        payment.confirmed_at = now
        payment.confirmed_by_role = "SISTEMA"
        payment.provider_payload = self._merge_response(payment.provider_payload, provider_response)
        payment.failure_reason = ""
        payment.save(update_fields=["status", "confirmed_at", "confirmed_by_role", "provider_payload", "failure_reason", "updated_at"])

        order = payment.order
        self._move_order(order, Order.Status.PAID, "SISTEMA", "", "Pago Stripe test confirmado", "PAYMENT_CONFIRMED", "Pago Stripe confirmado")
        self._move_order(order, Order.Status.AWAITING_CHEF_CONFIRMATION, "SISTEMA", "", "Pedido pagado y enviado al cocinero", "ORDER_READY_FOR_CHEF", "Pedido listo para gestion del cocinero")
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="STRIPE_PAYMENT_CONFIRMED",
            event_label="Pago Stripe confirmado",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"provider_response": provider_response},
        )
        self.notification_service.notify_payment_confirmed(order, payment)

    def _mark_pending(self, payment: OrderPayment, provider_response: dict):
        payment.status = OrderPayment.Status.PENDING
        payment.provider_payload = self._merge_response(payment.provider_payload, provider_response)
        payment.save(update_fields=["status", "provider_payload", "updated_at"])
        order = payment.order
        if order.status != Order.Status.PAYMENT_VALIDATING:
            self._move_order(order, Order.Status.PAYMENT_VALIDATING, "SISTEMA", "", "Pago Stripe sigue pendiente", "PAYMENT_PENDING", "Pago Stripe pendiente")
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="STRIPE_PAYMENT_PENDING",
            event_label="Pago Stripe pendiente",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"provider_response": provider_response},
        )

    def _expire_payment(self, payment: OrderPayment, provider_response: dict):
        payment.status = OrderPayment.Status.EXPIRED
        payment.failure_reason = "Stripe expired"
        payment.provider_payload = self._merge_response(payment.provider_payload, provider_response)
        payment.save(update_fields=["status", "failure_reason", "provider_payload", "updated_at"])
        order = payment.order
        if order.stock_reserved:
            self.stock_service.release_order_stock(order)
        self._move_order(order, Order.Status.EXPIRED, "SISTEMA", "", "Pago Stripe expirado", "PAYMENT_EXPIRED", "Pago Stripe expirado")
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="STRIPE_PAYMENT_EXPIRED",
            event_label="Pago Stripe expirado",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"provider_response": provider_response},
        )

    def _fail_payment(self, payment: OrderPayment, provider_response: dict, reason: str):
        payment.status = OrderPayment.Status.FAILED
        payment.failure_reason = reason[:255]
        payment.provider_payload = self._merge_response(payment.provider_payload, provider_response)
        payment.save(update_fields=["status", "failure_reason", "provider_payload", "updated_at"])
        order = payment.order
        if order.stock_reserved:
            self.stock_service.release_order_stock(order)
        self._move_order(order, Order.Status.PAYMENT_FAILED, "SISTEMA", "", reason, "PAYMENT_FAILED", "Pago Stripe fallido")
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="STRIPE_PAYMENT_FAILED",
            event_label="Pago Stripe fallido",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"provider_response": provider_response},
        )

    def _return_payload(self, payment: OrderPayment, *, handled: bool):
        payment.refresh_from_db()
        order = payment.order
        return {
            "handled": handled,
            "provider": payment.provider,
            "payment_status": payment.status,
            "order_status": order.status,
            "order_id": order.id,
            "payment_url": payment.payment_url,
            "external_reference": payment.external_reference,
        }

    def _find_client_payment(self, client: UserProfile, stripe_session_id: str):
        query = Q(order__client=client, method=Order.PaymentMethod.STRIPE_TEST)
        if stripe_session_id:
            query &= Q(external_reference=stripe_session_id)
        return OrderPayment.objects.filter(query).select_related("order").order_by("-created_at").first()

    def _move_order(self, order: Order, to_status: str, actor_role: str, actor_id: str, notes: str, event_code: str, event_label: str):
        from_status = order.status
        if from_status == to_status:
            return
        order.status = to_status
        order.last_status_at = timezone.now()
        order.save(update_fields=["status", "last_status_at", "updated_at"])
        OrderStatusHistory.objects.create(
            order=order,
            from_status=from_status,
            to_status=to_status,
            actor_role=actor_role,
            actor_id=str(actor_id),
            notes=notes,
        )
        OrderTimelineEvent.objects.create(
            order=order,
            event_code=event_code,
            event_label=event_label,
            actor_role=actor_role,
            actor_id=str(actor_id),
            metadata={"from_status": from_status, "to_status": to_status},
        )

    def _require_client(self, user_id: str):
        parsed = self._parse_uuid(user_id)
        client = UserProfile.objects.filter(supabase_user_id=parsed, role=UserProfile.ROLE_CLIENT).first() if parsed else None
        if not client:
            raise OrderStripeServiceError("Perfil de cliente no encontrado.", "client_not_found")
        return client

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _response_value(self, response, key):
        if isinstance(response, dict):
            return response.get(key)
        return getattr(response, key, None)

    def _response_dict(self, response):
        if isinstance(response, dict):
            return response
        if hasattr(response, "to_dict_recursive"):
            return response.to_dict_recursive()
        if hasattr(response, "to_dict"):
            return response.to_dict()
        return {
            key: value
            for key, value in getattr(response, "__dict__", {}).items()
            if not key.startswith("_")
        }

    def _merge_response(self, current: dict | None, incoming: dict | None):
        data = dict(current or {})
        if incoming:
            data["callback"] = incoming
        return data


def with_query_param(url, key, value):
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(item_key == key for item_key, _ in query):
        return url
    separator = "&" if parts.query else ""
    next_query = f"{parts.query}{separator}{key}={value}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, next_query, parts.fragment))
