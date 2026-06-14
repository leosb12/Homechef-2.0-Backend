from decimal import Decimal
from uuid import UUID

from django.db import transaction

from modules.gestion_cocinero.models import ChefAvailability, Dish
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import Cart, CartItem
from modules.pedidos_checkout_pagos.services.stock_service import (
    DishStockService,
    StockValidationError,
)


class CartServiceError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class CartService:
    def __init__(self):
        self.stock_service = DishStockService()

    def get_cart_summary(self, user_id: str):
        client = self._require_client(user_id)
        carts = (
            Cart.objects.filter(client=client, status=Cart.Status.ACTIVE)
            .select_related("chef", "chef__chef_profile")
            .prefetch_related("items__dish")
            .order_by("-updated_at")
        )

        payload_carts = [self._serialize_cart(cart) for cart in carts]
        items_count = sum(cart["items_count"] for cart in payload_carts)
        subtotal = sum(Decimal(str(cart["subtotal"])) for cart in payload_carts) if payload_carts else Decimal("0")
        return {
            "status": "ok",
            "message": "Carrito cargado correctamente." if payload_carts else "Tu carrito esta vacio.",
            "carts": payload_carts,
            "summary": {
                "carts_count": len(payload_carts),
                "items_count": items_count,
                "subtotal": float(subtotal),
                "currency": "BOB",
            },
        }

    @transaction.atomic
    def add_item(self, user_id: str, dish_id: str, quantity: int, fulfillment_type: str = ""):
        client = self._require_client(user_id)
        dish = (
            Dish.objects.filter(id=str(dish_id), deleted_at__isnull=True)
            .select_related("chef", "chef__availability", "chef__chef_profile")
            .first()
        )
        if not dish:
            raise CartServiceError("Plato no disponible.", "dish_not_found")

        normalized_mode = self._normalize_fulfillment_type(fulfillment_type)
        try:
            snapshot = self.stock_service.validate_dish_request(dish.id, quantity, normalized_mode)
        except StockValidationError as exc:
            raise self._map_stock_error(exc)

        if not self._chef_has_any_enabled_mode(dish.chef):
            raise CartServiceError("El cocinero no tiene modalidades habilitadas.", "modality_not_allowed")

        cart, _ = Cart.objects.get_or_create(
            client=client,
            chef=dish.chef,
            status=Cart.Status.ACTIVE,
            defaults={"currency": "BOB"},
        )

        item = cart.items.filter(dish=dish).first()
        next_quantity = quantity + (item.quantity if item else 0)
        if next_quantity > snapshot.available_portions:
            raise CartServiceError(
                "No hay porciones suficientes.",
                "insufficient_portions",
                {"available_portions": snapshot.available_portions},
            )

        price = Decimal(str(dish.price))
        subtotal = price * next_quantity
        if item:
            item.quantity = next_quantity
            item.unit_price = price
            item.subtotal = subtotal
            item.dish_name_snapshot = dish.name
            item.dish_image_url_snapshot = dish.image_url or ""
            item.save()
        else:
            item = CartItem.objects.create(
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
            "message": "Plato agregado al carrito correctamente.",
            "cart": self._serialize_cart(cart),
            "item": self._serialize_item(item),
        }

    @transaction.atomic
    def update_item(self, user_id: str, item_id: str, quantity: int, fulfillment_type: str = ""):
        client = self._require_client(user_id)
        item = (
            CartItem.objects.filter(id=str(item_id), cart__client=client, cart__status=Cart.Status.ACTIVE)
            .select_related("cart", "dish", "dish__chef", "dish__chef__availability", "dish__chef__chef_profile")
            .first()
        )
        if not item:
            raise CartServiceError("Item del carrito no encontrado.", "cart_item_not_found")

        normalized_mode = self._normalize_fulfillment_type(fulfillment_type)
        try:
            snapshot = self.stock_service.validate_dish_request(item.dish_id, quantity, normalized_mode)
        except StockValidationError as exc:
            raise self._map_stock_error(exc)

        item.quantity = quantity
        item.unit_price = Decimal(str(item.dish.price))
        item.subtotal = item.unit_price * quantity
        item.dish_name_snapshot = item.dish.name
        item.dish_image_url_snapshot = item.dish.image_url or ""
        item.save()

        item.cart.refresh_from_db()
        return {
            "message": "Cantidad actualizada correctamente.",
            "cart": self._serialize_cart(item.cart),
            "item": self._serialize_item(item, available_portions=snapshot.available_portions),
        }

    @transaction.atomic
    def remove_item(self, user_id: str, item_id: str):
        client = self._require_client(user_id)
        item = CartItem.objects.filter(id=str(item_id), cart__client=client, cart__status=Cart.Status.ACTIVE).select_related("cart").first()
        if not item:
            raise CartServiceError("Item del carrito no encontrado.", "cart_item_not_found")

        cart = item.cart
        item.delete()
        if not cart.items.exists():
            cart.status = Cart.Status.ABANDONED
            cart.save(update_fields=["status", "updated_at"])
            payload_cart = None
        else:
            cart.refresh_from_db()
            payload_cart = self._serialize_cart(cart)
        return {
            "message": "Item eliminado del carrito.",
            "cart": payload_cart,
            "removed_item_id": str(item_id),
        }

    def _require_client(self, user_id: str):
        parsed = self._parse_uuid(user_id)
        client = UserProfile.objects.filter(supabase_user_id=parsed, role=UserProfile.ROLE_CLIENT).first() if parsed else None
        if not client:
            raise CartServiceError("Perfil de cliente no encontrado.", "client_not_found")
        return client

    def _serialize_cart(self, cart: Cart):
        items = [self._serialize_item(item) for item in cart.items.select_related("dish").all().order_by("created_at")]
        subtotal = sum(Decimal(str(item["subtotal"])) for item in items) if items else Decimal("0")
        return {
            "id": cart.id,
            "chef": {
                "id": str(cart.chef.supabase_user_id),
                "name": self._chef_name(cart.chef),
                "location": self._chef_location(cart.chef),
            },
            "currency": cart.currency,
            "status": cart.status,
            "items_count": sum(item["quantity"] for item in items),
            "subtotal": float(subtotal),
            "items": items,
            "updated_at": cart.updated_at,
        }

    def _serialize_item(self, item: CartItem, available_portions: int | None = None):
        portions = available_portions
        if portions is None:
            try:
                portions = self.stock_service.get_snapshot(item.dish_id).available_portions
            except StockValidationError:
                portions = 0
        return {
            "id": item.id,
            "cart_id": item.cart_id,
            "dish_id": item.dish_id,
            "dish_name": item.dish_name_snapshot,
            "dish_image_url": item.dish_image_url_snapshot,
            "chef_id": str(item.cart.chef.supabase_user_id),
            "chef_name": self._chef_name(item.cart.chef),
            "chef_location": self._chef_location(item.cart.chef),
            "quantity": item.quantity,
            "unit_price": float(item.unit_price),
            "subtotal": float(item.subtotal),
            "available_portions": portions,
        }

    def _chef_name(self, chef: UserProfile):
        profile = getattr(chef, "chef_profile", None)
        if profile and getattr(profile, "business_name", ""):
            return profile.business_name
        full_name = f"{chef.first_name} {chef.last_name}".strip()
        return full_name or "Cocinero HomeChef"

    def _chef_location(self, chef: UserProfile):
        profile = getattr(chef, "chef_profile", None)
        return {
            "latitude": getattr(profile, "location_latitude", None),
            "longitude": getattr(profile, "location_longitude", None),
            "address": getattr(profile, "location_address", "") if profile else "",
        }

    def _normalize_fulfillment_type(self, fulfillment_type: str):
        return str(fulfillment_type or "").strip().lower()

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _map_stock_error(self, exc: StockValidationError):
        if exc.code == "invalid_quantity":
            return CartServiceError("Cantidad no valida.", "invalid_quantity", exc.details)
        if exc.code == "chef_unavailable":
            return CartServiceError("Cocinero no disponible temporalmente.", "chef_unavailable", exc.details)
        if exc.code == "modality_not_allowed":
            return CartServiceError("La modalidad seleccionada no esta disponible.", "modality_not_allowed", exc.details)
        if exc.code == "insufficient_portions":
            return CartServiceError("No hay porciones suficientes.", "insufficient_portions", exc.details)
        return CartServiceError("Plato no disponible.", exc.code or "dish_unavailable", exc.details)

    def _chef_has_any_enabled_mode(self, chef: UserProfile):
        availability = ChefAvailability.objects.filter(chef=chef).first()
        if not availability:
            return False
        return bool(availability.accept_delivery or availability.accept_pickup)
