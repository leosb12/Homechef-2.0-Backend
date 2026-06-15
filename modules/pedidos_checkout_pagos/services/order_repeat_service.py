from decimal import Decimal
from uuid import UUID

from django.db import transaction

from modules.gestion_cocinero.models import ChefAvailability, Dish
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import Cart, CartItem, Order, OrderTimelineEvent
from modules.pedidos_checkout_pagos.services.cart_service import CartService
from modules.pedidos_checkout_pagos.services.stock_service import DishStockService, StockValidationError


class OrderRepeatServiceError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class OrderRepeatService:
    def __init__(self):
        self.stock_service = DishStockService()
        self.cart_service = CartService()

    @transaction.atomic
    def repeat_client_order(self, user_id: str, order_id: str):
        client = self._require_client(user_id)
        order = self._get_order_for_client(client, order_id)
        fulfillment_type = str(order.fulfillment_type or "").strip().lower()

        added_items = []
        skipped_items = []
        touched_cart = None

        for order_item in order.items.select_related("dish", "dish__chef", "dish__chef__availability", "dish__chef__chef_profile").all().order_by("created_at"):
            result = self._repeat_order_item(client, order_item, fulfillment_type)
            if result["status"] == "added":
                added_items.append(result["item"])
                touched_cart = result["cart"]
                continue
            skipped_items.append(result["item"])

        self._register_timeline_event(
            order,
            client,
            touched_cart,
            added_count=len(added_items),
            skipped_count=len(skipped_items),
        )

        payload_cart = self.cart_service._serialize_cart(touched_cart) if touched_cart else None
        return {
            "status": "ok",
            "message": self._build_message(added_items, skipped_items),
            "order_id": order.id,
            "cart": payload_cart,
            "summary": {
                "requested_items": len(added_items) + len(skipped_items),
                "added_items": len(added_items),
                "skipped_items": len(skipped_items),
                "cart_id": touched_cart.id if touched_cart else "",
            },
            "added_items": added_items,
            "skipped_items": skipped_items,
        }

    def _repeat_order_item(self, client: UserProfile, order_item, fulfillment_type: str):
        dish = self._load_dish(order_item.dish_id)
        if not dish:
            return {
                "status": "skipped",
                "item": self._serialize_skipped_item(
                    order_item,
                    "dish_not_found",
                    "El plato ya no existe o fue retirado.",
                ),
            }

        if dish.deleted_at is not None:
            return {
                "status": "skipped",
                "item": self._serialize_skipped_item(
                    order_item,
                    "dish_not_found",
                    "El plato fue retirado del catalogo.",
                ),
            }

        if dish.status != Dish.STATUS_PUBLISHED:
            return {
                "status": "skipped",
                "item": self._serialize_skipped_item(
                    order_item,
                    "dish_unpublished",
                    "El plato ya no esta publicado.",
                ),
            }

        if not self._chef_has_any_enabled_mode(dish.chef):
            return {
                "status": "skipped",
                "item": self._serialize_skipped_item(
                    order_item,
                    "modality_not_allowed",
                    "El cocinero no tiene modalidades habilitadas en este momento.",
                ),
            }

        try:
            snapshot = self.stock_service.validate_dish_request(
                dish.id,
                order_item.quantity,
                fulfillment_type,
            )
        except StockValidationError as exc:
            return {
                "status": "skipped",
                "item": self._serialize_skipped_item(
                    order_item,
                    self._map_stock_error_code(exc.code),
                    self._map_stock_error_message(exc),
                    details=exc.details,
                ),
            }

        cart, _ = Cart.objects.get_or_create(
            client=client,
            chef=dish.chef,
            status=Cart.Status.ACTIVE,
            defaults={"currency": "BOB"},
        )
        cart_item = cart.items.filter(dish=dish).select_related("cart").first()
        next_quantity = order_item.quantity + (cart_item.quantity if cart_item else 0)
        if next_quantity > snapshot.available_portions:
            return {
                "status": "skipped",
                "item": self._serialize_skipped_item(
                    order_item,
                    "insufficient_portions",
                    "No hay porciones suficientes para repetir este plato con la cantidad actual del carrito.",
                    details={"available_portions": snapshot.available_portions},
                ),
            }

        price = Decimal(str(dish.price))
        subtotal = price * next_quantity
        if cart_item:
            cart_item.quantity = next_quantity
            cart_item.unit_price = price
            cart_item.subtotal = subtotal
            cart_item.dish_name_snapshot = dish.name
            cart_item.dish_image_url_snapshot = dish.image_url or ""
            cart_item.save()
        else:
            cart_item = CartItem.objects.create(
                cart=cart,
                dish=dish,
                quantity=next_quantity,
                unit_price=price,
                subtotal=subtotal,
                dish_name_snapshot=dish.name,
                dish_image_url_snapshot=dish.image_url or "",
            )

        cart.refresh_from_db()
        return {
            "status": "added",
            "cart": cart,
            "item": {
                "order_item_id": order_item.id,
                "cart_item_id": cart_item.id,
                "dish_id": dish.id,
                "dish_name": dish.name,
                "requested_quantity": order_item.quantity,
                "cart_quantity": cart_item.quantity,
                "unit_price": float(price),
                "subtotal": float(cart_item.subtotal),
            },
        }

    def _load_dish(self, dish_id: str):
        return (
            Dish.objects.filter(id=str(dish_id))
            .select_related("chef", "chef__availability", "chef__chef_profile")
            .first()
        )

    def _serialize_skipped_item(self, order_item, code: str, reason: str, details: dict | None = None):
        payload = {
            "order_item_id": order_item.id,
            "dish_id": order_item.dish_id,
            "dish_name": order_item.dish_name_snapshot,
            "requested_quantity": order_item.quantity,
            "code": code,
            "reason": reason,
        }
        if details:
            payload["details"] = details
        return payload

    def _build_message(self, added_items: list, skipped_items: list):
        if added_items and not skipped_items:
            return "Pedido repetido y agregado al carrito correctamente."
        if added_items and skipped_items:
            return "Se repitio el pedido de forma parcial. Algunos platos se agregaron y otros se omitieron por disponibilidad actual."
        return "No se pudo repetir el pedido porque ninguno de sus platos sigue disponible para compra."

    def _register_timeline_event(self, order: Order, client: UserProfile, cart: Cart | None, *, added_count: int, skipped_count: int):
        OrderTimelineEvent.objects.create(
            order=order,
            event_code="ORDER_REPEATED_TO_CART",
            event_label="Pedido repetido hacia carrito",
            actor_role="CLIENTE",
            actor_id=str(client.supabase_user_id),
            metadata={
                "cart_id": cart.id if cart else "",
                "chef_id": str(order.chef.supabase_user_id),
                "added_items": added_count,
                "skipped_items": skipped_count,
            },
        )

    def _map_stock_error_code(self, code: str):
        if code == "stock_unavailable":
            return "insufficient_portions"
        return code or "dish_unavailable"

    def _map_stock_error_message(self, exc: StockValidationError):
        if exc.code == "invalid_quantity":
            return "La cantidad original del plato ya no es valida."
        if exc.code == "chef_unavailable":
            return "El cocinero no esta operativo en este momento."
        if exc.code == "modality_not_allowed":
            return "La modalidad original del pedido ya no esta habilitada para este plato."
        if exc.code == "stock_unavailable":
            return "No hay porciones suficientes disponibles actualmente."
        return "El plato ya no se puede pedir con las condiciones actuales."

    def _get_order_for_client(self, client: UserProfile, order_id: str):
        order = (
            Order.objects.filter(id=str(order_id), client=client)
            .select_related("chef")
            .prefetch_related("items")
            .first()
        )
        if not order:
            raise OrderRepeatServiceError("Pedido no encontrado para el cliente.", "order_not_found")
        return order

    def _require_client(self, user_id: str):
        parsed = self._parse_uuid(user_id)
        client = UserProfile.objects.filter(supabase_user_id=parsed, role=UserProfile.ROLE_CLIENT).first() if parsed else None
        if not client:
            raise OrderRepeatServiceError("Perfil de cliente no encontrado.", "client_not_found")
        return client

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _chef_has_any_enabled_mode(self, chef: UserProfile):
        availability = ChefAvailability.objects.filter(chef=chef).first()
        if not availability:
            return False
        return bool(availability.accept_delivery or availability.accept_pickup)
