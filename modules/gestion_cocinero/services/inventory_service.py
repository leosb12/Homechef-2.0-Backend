from django.db import transaction
from django.utils import timezone
import datetime

from modules.gestion_cocinero.models import DishIngredient, InventoryItem, StockMovement
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.confianza_administracion_seguridad.services.email_service import EmailService
from modules.confianza_administracion_seguridad.services.notification_service import NotificationService


class InventoryService:
    def list_inventory(self, chef_id: str):
        items = InventoryItem.objects.filter(chef__supabase_user_id=chef_id, deleted_at__isnull=True).order_by("name")
        return [
            {
                "id": item.id,
                "name": item.name,
                "unit_of_measure": item.unit_of_measure,
                "current_stock": float(item.current_stock),
                "low_stock_threshold": float(item.low_stock_threshold),
                "expiration_date": item.expiration_date.isoformat() if item.expiration_date else None,
                "is_active": item.is_active,
                "status": "expired" if (item.expiration_date and item.expiration_date < timezone.localdate()) else ("low_stock" if item.current_stock <= item.low_stock_threshold else "ok"),
            }
            for item in items
        ]

    @transaction.atomic
    def save_inventory_item(self, chef_id: str, payload: dict):
        user = UserProfile.objects.filter(supabase_user_id=chef_id).first()
        if not user:
            raise ValueError("Usuario no encontrado.")

        item_id = payload.get("id")
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("El nombre del insumo es obligatorio.")

        unit = str(payload.get("unit_of_measure", "")).strip()
        if not unit:
            raise ValueError("La unidad de medida es obligatoria.")

        try:
            current_stock = float(payload.get("current_stock", 0))
            low_stock_threshold = float(payload.get("low_stock_threshold", 0))
            
            expiration_date = None
            exp_str = payload.get("expiration_date")
            if exp_str:
                expiration_date = datetime.date.fromisoformat(exp_str[:10])
        except (TypeError, ValueError):
            raise ValueError("Valores numéricos o de fecha inválidos.")

        if item_id:
            item = InventoryItem.objects.filter(id=item_id, chef=user, deleted_at__isnull=True).first()
            if not item:
                raise ValueError("Insumo no encontrado.")
            
            if item.name != name and InventoryItem.objects.filter(chef=user, name=name, deleted_at__isnull=True).exists():
                raise ValueError(f"Ya tienes un insumo registrado como '{name}'.")
            
            # Registrar movimiento si el stock cambió manualmente
            stock_diff = current_stock - float(item.current_stock)
            if stock_diff != 0:
                StockMovement.objects.create(
                    inventory_item=item,
                    quantity_change=stock_diff,
                    movement_type=StockMovement.MOVEMENT_ADJUSTMENT,
                    notes="Ajuste manual desde dashboard"
                )
            
            item.name = name
            item.unit_of_measure = unit
            item.current_stock = current_stock
            item.low_stock_threshold = low_stock_threshold
            item.expiration_date = expiration_date
            item.is_active = bool(payload.get("is_active", True))
            item.save()
        else:
            if InventoryItem.objects.filter(chef=user, name=name, deleted_at__isnull=True).exists():
                raise ValueError(f"Ya tienes un insumo registrado como '{name}'. Si quieres actualizar su stock, edita el existente.")
                
            item = InventoryItem.objects.create(
                chef=user,
                name=name,
                unit_of_measure=unit,
                current_stock=current_stock,
                low_stock_threshold=low_stock_threshold,
                expiration_date=expiration_date,
                is_active=bool(payload.get("is_active", True))
            )
            if current_stock > 0:
                StockMovement.objects.create(
                    inventory_item=item,
                    quantity_change=current_stock,
                    movement_type=StockMovement.MOVEMENT_PURCHASE,
                    notes="Stock inicial"
                )

        return {
            "id": item.id,
            "name": item.name,
            "unit_of_measure": item.unit_of_measure,
            "current_stock": float(item.current_stock),
            "low_stock_threshold": float(item.low_stock_threshold),
            "expiration_date": item.expiration_date.isoformat() if item.expiration_date else None,
            "is_active": item.is_active,
        }

    def delete_inventory_item(self, chef_id: str, item_id: int):
        user = UserProfile.objects.filter(supabase_user_id=chef_id).first()
        item = InventoryItem.objects.filter(id=item_id, chef=user, deleted_at__isnull=True).first()
        if not item:
            raise ValueError("Insumo no encontrado.")
        item.deleted_at = timezone.now()
        item.save(update_fields=["deleted_at"])

    @transaction.atomic
    def deduct_stock_from_order(self, order_id: str, order_items_payload: list):
        # Esta funcion será llamada desde pedidos_checkout_pagos cuando la orden sea pagada.
        # order_items_payload = [{"dish_id": "uuid", "portions": 2}, ...]
        for order_item in order_items_payload:
            dish_id = order_item.get("dish_id")
            portions_sold = int(order_item.get("portions", 1))
            
            # Buscar todos los insumos que gasta este plato
            recipes = DishIngredient.objects.filter(dish_id=dish_id).select_related("inventory_item")
            for recipe in recipes:
                inventory_item = recipe.inventory_item
                amount_to_deduct = float(recipe.quantity_required) * portions_sold
                
                # Descontar del inventario real
                previous_stock = float(inventory_item.current_stock)
                threshold = float(inventory_item.low_stock_threshold)
                
                inventory_item.current_stock = previous_stock - amount_to_deduct
                inventory_item.save(update_fields=["current_stock", "updated_at"])
                
                # Check for low stock crossing to prevent spam
                new_stock = float(inventory_item.current_stock)
                if previous_stock > threshold and new_stock <= threshold:
                    # Enviar alerta asíncrona
                    EmailService.send_low_stock_alert(
                        chef_email=inventory_item.chef.email,
                        chef_name=inventory_item.chef.first_name or "Cocinero",
                        item_name=inventory_item.name,
                        current_stock=new_stock,
                        threshold=threshold
                    )
                    # Enviar notificacion in-app
                    try:
                        NotificationService().notify_low_stock(chef_user=inventory_item.chef, item=inventory_item)
                    except Exception as e:
                        print(f"Error sending in-app low stock notification: {e}")
                
                # Registrar historial
                StockMovement.objects.create(
                    inventory_item=inventory_item,
                    quantity_change=-amount_to_deduct,
                    movement_type=StockMovement.MOVEMENT_SALE,
                    reference_id=str(order_id),
                    notes=f"Venta de plato {recipe.dish.name} ({portions_sold} porciones)"
                )

    @transaction.atomic
    def restore_stock_from_order(self, order_id: str, order_items_payload: list):
        # Esta funcion será llamada si se cancela o rechaza un pedido despues de haber descontado
        for order_item in order_items_payload:
            dish_id = order_item.get("dish_id")
            portions_sold = int(order_item.get("portions", 1))
            
            recipes = DishIngredient.objects.filter(dish_id=dish_id).select_related("inventory_item")
            for recipe in recipes:
                inventory_item = recipe.inventory_item
                amount_to_restore = float(recipe.quantity_required) * portions_sold
                
                inventory_item.current_stock = float(inventory_item.current_stock) + amount_to_restore
                inventory_item.save(update_fields=["current_stock", "updated_at"])
                
                StockMovement.objects.create(
                    inventory_item=inventory_item,
                    quantity_change=amount_to_restore,
                    movement_type=StockMovement.MOVEMENT_ADJUSTMENT,
                    reference_id=str(order_id),
                    notes=f"Restauracion por pedido cancelado/rechazado {recipe.dish.name} ({portions_sold} porciones)"
                )

