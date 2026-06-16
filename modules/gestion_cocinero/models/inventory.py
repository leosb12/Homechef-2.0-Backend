from django.db import models
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from . import Dish


class InventoryItem(models.Model):
    chef = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="inventory_items")
    name = models.CharField(max_length=160)
    unit_of_measure = models.CharField(max_length=50)
    current_stock = models.DecimalField(max_digits=12, decimal_places=4, default=0.0)
    low_stock_threshold = models.DecimalField(max_digits=12, decimal_places=4, default=0.0)
    expiration_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "chef_inventory_items"
        indexes = [
            models.Index(fields=["chef", "name"]),
            models.Index(fields=["deleted_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.unit_of_measure}) - {self.chef.email}"


class DishIngredient(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="dish_ingredients")
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="used_in_dishes")
    quantity_required = models.DecimalField(max_digits=12, decimal_places=4)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "chef_dish_ingredients"
        unique_together = ("dish", "inventory_item")

    def __str__(self):
        return f"{self.quantity_required} {self.inventory_item.unit_of_measure} de {self.inventory_item.name} para {self.dish.name}"


class StockMovement(models.Model):
    MOVEMENT_SALE = "sale"
    MOVEMENT_PURCHASE = "purchase"
    MOVEMENT_ADJUSTMENT = "adjustment"
    MOVEMENT_EXPIRED = "expired"

    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="movements")
    quantity_change = models.DecimalField(max_digits=12, decimal_places=4)
    movement_type = models.CharField(max_length=30)
    reference_id = models.CharField(max_length=128, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chef_stock_movements"
        indexes = [
            models.Index(fields=["inventory_item", "created_at"]),
        ]

    def __str__(self):
        return f"{self.movement_type}: {self.quantity_change} on {self.inventory_item.name}"
