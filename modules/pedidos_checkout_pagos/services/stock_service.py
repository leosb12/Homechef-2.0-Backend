from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from modules.gestion_cocinero.models import ChefAvailability, DailyMenu, DailyMenuItem, Dish
from modules.gestion_cocinero.services.availability_rules import is_open_now
from modules.pedidos_checkout_pagos.models import Order, OrderItem


class StockValidationError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class DishStockSnapshot:
    dish_id: str
    source_type: str
    source_ref: str
    available_portions: int
    is_available: bool
    chef_is_available: bool
    modality_allowed: bool
    uses_active_menu: bool
    source_status: str
    reason_code: str


def build_dish_stock_snapshot(
    *,
    dish: Dish,
    menu: DailyMenu | None = None,
    menu_item: DailyMenuItem | None = None,
    availability: ChefAvailability | None = None,
    fulfillment_type: str = "",
):
    normalized_mode = str(fulfillment_type or "").strip().lower()
    chef_is_available = _chef_is_available(availability)
    modality_allowed = _mode_allowed(availability, normalized_mode)
    uses_active_menu = bool(menu and menu.is_active and menu_item is not None)

    if uses_active_menu:
        source_type = OrderItem.StockSourceType.DAILY_MENU_ITEM
        source_ref = str(menu_item.id)
        source_status = str(menu_item.status or DailyMenuItem.STATUS_AVAILABLE)
        available_portions = int(menu_item.portions or 0)
        source_available = source_status == DailyMenuItem.STATUS_AVAILABLE and available_portions > 0
    else:
        source_type = OrderItem.StockSourceType.DISH
        source_ref = str(dish.id)
        source_status = str(dish.status or Dish.STATUS_DRAFT)
        available_portions = int(dish.portions or 0)
        source_available = source_status == Dish.STATUS_PUBLISHED and available_portions > 0

    reason_code = ""
    if dish.deleted_at is not None or dish.status != Dish.STATUS_PUBLISHED or dish.revision_status in ["oculta_temporalmente", "rechazada"]:
        reason_code = "dish_unpublished"
    elif not chef_is_available:
        reason_code = "chef_unavailable"
    elif not modality_allowed:
        reason_code = "modality_not_allowed"
    elif not source_available:
        reason_code = "stock_unavailable"

    return DishStockSnapshot(
        dish_id=str(dish.id),
        source_type=source_type,
        source_ref=source_ref,
        available_portions=available_portions,
        is_available=reason_code == "",
        chef_is_available=chef_is_available,
        modality_allowed=modality_allowed,
        uses_active_menu=uses_active_menu,
        source_status=source_status,
        reason_code=reason_code,
    )


class DishStockService:
    def get_snapshot(self, dish_id: str, fulfillment_type: str = "", lock: bool = False):
        dish_queryset = Dish.objects.filter(id=str(dish_id), deleted_at__isnull=True).select_related("chef")
        if lock:
            dish_queryset = dish_queryset.select_for_update()
        dish = dish_queryset.first()
        if not dish:
            raise StockValidationError("Plato no disponible.", "dish_not_found")

        menu, menu_item = self._resolve_menu_source(dish.chef_id, dish.id, lock=lock)
        availability = ChefAvailability.objects.filter(chef=dish.chef).first()
        return build_dish_stock_snapshot(
            dish=dish,
            menu=menu,
            menu_item=menu_item,
            availability=availability,
            fulfillment_type=fulfillment_type,
        )

    def validate_dish_request(self, dish_id: str, quantity: int, fulfillment_type: str = ""):
        if int(quantity or 0) <= 0:
            raise StockValidationError("Cantidad no valida.", "invalid_quantity")
        snapshot = self.get_snapshot(dish_id, fulfillment_type=fulfillment_type)
        if not snapshot.is_available:
            raise StockValidationError(
                "Plato no disponible.",
                snapshot.reason_code or "dish_unavailable",
                {"available_portions": snapshot.available_portions},
            )
        if quantity > snapshot.available_portions:
            raise StockValidationError(
                "No hay porciones suficientes.",
                "insufficient_portions",
                {"available_portions": snapshot.available_portions},
            )
        return snapshot

    @transaction.atomic
    def reserve_order_stock(self, order: Order):
        locked_order = Order.objects.select_for_update().filter(id=order.id).first()
        if not locked_order:
            raise StockValidationError("Pedido no encontrado.", "order_not_found")
        if locked_order.stock_reserved:
            return locked_order

        items = list(locked_order.items.select_related("dish").all())
        if not items:
            raise StockValidationError("El pedido no tiene items para reservar.", "order_without_items")

        now = timezone.now()
        changed_items = []
        for item in items:
            snapshot = self.get_snapshot(locked_order_item_dish_id(item), fulfillment_type=locked_order.fulfillment_type, lock=True)
            if not snapshot.is_available:
                raise StockValidationError(
                    "No se pudo reservar stock para el pedido.",
                    snapshot.reason_code or "dish_unavailable",
                    {"dish_id": str(item.dish_id), "available_portions": snapshot.available_portions},
                )
            if item.quantity > snapshot.available_portions:
                raise StockValidationError(
                    "No hay porciones suficientes para completar el pedido.",
                    "insufficient_portions",
                    {"dish_id": str(item.dish_id), "available_portions": snapshot.available_portions},
                )

            self._apply_stock_delta(snapshot, item.quantity * -1, now)
            item.stock_source_type = snapshot.source_type
            item.stock_source_ref = snapshot.source_ref
            changed_items.append(item)

        if changed_items:
            OrderItem.objects.bulk_update(changed_items, ["stock_source_type", "stock_source_ref"])

        locked_order.stock_reserved = True
        locked_order.save(update_fields=["stock_reserved", "updated_at"])
        return locked_order

    @transaction.atomic
    def release_order_stock(self, order: Order):
        locked_order = Order.objects.select_for_update().filter(id=order.id).first()
        if not locked_order:
            raise StockValidationError("Pedido no encontrado.", "order_not_found")
        if not locked_order.stock_reserved:
            return locked_order

        items = list(locked_order.items.select_related("dish").all())
        now = timezone.now()
        for item in items:
            source_type = item.stock_source_type or OrderItem.StockSourceType.DISH
            source_ref = item.stock_source_ref or str(item.dish_id)
            self._restore_stock(source_type=source_type, source_ref=source_ref, dish_id=str(item.dish_id), quantity=item.quantity, now=now)

        locked_order.stock_reserved = False
        locked_order.save(update_fields=["stock_reserved", "updated_at"])
        return locked_order

    def _resolve_menu_source(self, chef_id: int, dish_id: str, lock: bool = False):
        queryset = DailyMenu.objects.filter(chef_id=chef_id, is_active=True, deleted_at__isnull=True).prefetch_related("items")
        if lock:
            queryset = queryset.select_for_update()
        menu = queryset.first()
        if not menu:
            return None, None

        items_queryset = menu.items.filter(dish_id=str(dish_id))
        if lock:
            items_queryset = items_queryset.select_for_update()
        return menu, items_queryset.first()

    def _apply_stock_delta(self, snapshot: DishStockSnapshot, delta: int, now):
        if snapshot.source_type == OrderItem.StockSourceType.DAILY_MENU_ITEM:
            source = DailyMenuItem.objects.select_for_update().filter(id=int(snapshot.source_ref)).first()
            if not source:
                raise StockValidationError("No se encontro el item del menu para reservar stock.", "menu_item_not_found")
            next_portions = int(source.portions or 0) + delta
            if next_portions < 0:
                raise StockValidationError("No hay porciones suficientes.", "insufficient_portions", {"available_portions": int(source.portions or 0)})
            source.portions = next_portions
            source.save(update_fields=["portions"])
            return

        source = Dish.objects.select_for_update().filter(id=snapshot.dish_id, deleted_at__isnull=True).first()
        if not source:
            raise StockValidationError("No se encontro el plato para reservar stock.", "dish_not_found")
        next_portions = int(source.portions or 0) + delta
        if next_portions < 0:
            raise StockValidationError("No hay porciones suficientes.", "insufficient_portions", {"available_portions": int(source.portions or 0)})
        source.portions = next_portions
        source.updated_at = now
        source.save(update_fields=["portions", "updated_at"])

    def _restore_stock(self, *, source_type: str, source_ref: str, dish_id: str, quantity: int, now):
        if source_type == OrderItem.StockSourceType.DAILY_MENU_ITEM:
            source = DailyMenuItem.objects.select_for_update().filter(id=int(source_ref)).first()
            if source:
                source.portions = int(source.portions or 0) + int(quantity or 0)
                source.save(update_fields=["portions"])
                return

        source = Dish.objects.select_for_update().filter(id=str(dish_id), deleted_at__isnull=True).first()
        if source:
            source.portions = int(source.portions or 0) + int(quantity or 0)
            source.updated_at = now
            source.save(update_fields=["portions", "updated_at"])


def locked_order_item_dish_id(item: OrderItem):
    return str(item.dish_id)
def _chef_is_available(availability: ChefAvailability | None):
    if availability is None:
        return False
    return is_open_now({"is_active": availability.is_active, "weekly_schedule": availability.weekly_schedule})


def _mode_allowed(availability: ChefAvailability | None, fulfillment_type: str):
    if availability is None:
        return False
    if fulfillment_type == "delivery":
        return bool(availability.accept_delivery)
    if fulfillment_type == "pickup":
        return bool(availability.accept_pickup)
    return True
