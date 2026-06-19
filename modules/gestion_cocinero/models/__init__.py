from uuid import uuid4

from django.db import models

from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile


def uuid4_string():
    return str(uuid4())


class ChefProfile(models.Model):
    STATUS_PENDING = "pending_validation"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    user = models.OneToOneField(UserProfile, on_delete=models.CASCADE, related_name="chef_profile")
    business_name = models.CharField(max_length=160, blank=True)
    public_description = models.TextField(blank=True)
    specialties = models.JSONField(default=list, blank=True)
    location_latitude = models.FloatField(null=True, blank=True)
    location_longitude = models.FloatField(null=True, blank=True)
    location_address = models.CharField(max_length=255, blank=True)
    schedule = models.CharField(max_length=255, blank=True)
    profile_image_url = models.TextField(blank=True)
    status = models.CharField(max_length=40, default=STATUS_PENDING)
    kitchen_photos = models.JSONField(default=list, blank=True)
    ai_subscription_active = models.BooleanField(default=False)
    legacy_mongo_id = models.CharField(max_length=128, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "chef_profiles"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["deleted_at"]),
        ]

    def __str__(self):
        return self.business_name or self.user.email


class ChefAvailability(models.Model):
    chef = models.OneToOneField(UserProfile, on_delete=models.CASCADE, related_name="availability")
    is_active = models.BooleanField(default=True)
    weekly_schedule = models.JSONField(default=list, blank=True)
    pickup_schedule = models.CharField(max_length=255, blank=True)
    accept_delivery = models.BooleanField(default=True)
    accept_pickup = models.BooleanField(default=True)
    simultaneous_orders_limit = models.PositiveIntegerField(default=10)
    legacy_mongo_id = models.CharField(max_length=128, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "chef_availability"
        indexes = [models.Index(fields=["deleted_at"])]

    def __str__(self):
        return f"Availability for {self.chef.email}"


class Dish(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_PAUSED = "paused"
    STATUS_SOLD_OUT = "sold_out"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    chef = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="dishes")
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    portions = models.PositiveIntegerField(default=1)
    ingredients = models.JSONField(default=list, blank=True)
    tags = models.JSONField(default=list, blank=True)
    allergens = models.JSONField(default=list, blank=True)
    image_url = models.TextField(blank=True)
    schedule = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=30, default=STATUS_DRAFT)
    legacy_mongo_id = models.CharField(max_length=128, blank=True, db_index=True)
    revision_status = models.CharField(max_length=40, default="pendiente_revision_ia")
    ia_risk_score = models.IntegerField(null=True, blank=True)
    ia_text_risk_score = models.IntegerField(null=True, blank=True)
    ia_quality_review_id = models.CharField(max_length=64, blank=True)
    ia_quality_reasons = models.JSONField(default=list, blank=True)
    ia_quality_recommendation = models.TextField(blank=True)
    reported_count = models.IntegerField(default=0)
    last_quality_analysis_at = models.DateTimeField(null=True, blank=True)
    admin_reviewed_by = models.ForeignKey(UserProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_dishes")
    admin_reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_review_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "chef_dishes"
        indexes = [
            models.Index(fields=["chef", "status"]),
            models.Index(fields=["updated_at"]),
            models.Index(fields=["deleted_at"]),
        ]

    def __str__(self):
        return self.name


class DailyMenu(models.Model):
    chef = models.OneToOneField(UserProfile, on_delete=models.CASCADE, related_name="daily_menu")
    schedule = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=False)
    legacy_mongo_id = models.CharField(max_length=128, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "chef_daily_menu"
        indexes = [models.Index(fields=["deleted_at"])]

    def __str__(self):
        return f"Daily menu for {self.chef.email}"


class DailyMenuItem(models.Model):
    STATUS_AVAILABLE = "available"
    STATUS_UNAVAILABLE = "unavailable"
    STATUS_SOLD_OUT = "sold_out"

    menu = models.ForeignKey(DailyMenu, on_delete=models.CASCADE, related_name="items")
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="menu_items")
    portions = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=30, default=STATUS_AVAILABLE)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "chef_daily_menu_items"
        unique_together = ("menu", "dish")
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.dish.name} in {self.menu_id}"

from .inventory import InventoryItem, DishIngredient, StockMovement
