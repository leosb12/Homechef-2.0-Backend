from uuid import uuid4

from django.db import models

from modules.gestion_cocinero.models import Dish
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile


def uuid4_string():
    return str(uuid4())


class MarketplaceFavorite(models.Model):
    TYPE_DISH = "dish"
    TYPE_CHEF = "chef"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="favorites")
    favorite_type = models.CharField(max_length=20)
    ref_id = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "marketplace_favorites"
        unique_together = ("user", "favorite_type", "ref_id")
        indexes = [
            models.Index(fields=["favorite_type", "ref_id"]),
            models.Index(fields=["deleted_at"]),
        ]

    def __str__(self):
        return f"{self.user.email} -> {self.favorite_type}:{self.ref_id}"


class MarketplacePreference(models.Model):
    user = models.OneToOneField(UserProfile, on_delete=models.CASCADE, related_name="marketplace_preferences")
    cuisine_types = models.JSONField(default=list, blank=True)
    diet_types = models.JSONField(default=list, blank=True)
    price_range = models.JSONField(default=dict, blank=True)
    legacy_mongo_id = models.CharField(max_length=128, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "marketplace_preferences"
        indexes = [models.Index(fields=["deleted_at"])]

    def __str__(self):
        return f"Preferences for {self.user.email}"


class MarketplaceReview(models.Model):
    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    chef = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_reviews",
    )
    chef_ref_id = models.CharField(max_length=128, db_index=True)
    dish = models.ForeignKey(
        Dish,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="marketplace_reviews",
    )
    dish_ref_id = models.CharField(max_length=128, blank=True, db_index=True)
    author = models.CharField(max_length=120, blank=True)
    rating = models.PositiveSmallIntegerField(default=0)
    comment = models.TextField(blank=True)
    is_public = models.BooleanField(default=True)
    legacy_mongo_id = models.CharField(max_length=128, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "marketplace_reviews"
        indexes = [
            models.Index(fields=["chef_ref_id", "is_public"]),
            models.Index(fields=["dish_ref_id", "is_public"]),
            models.Index(fields=["rating"]),
            models.Index(fields=["deleted_at"]),
        ]

    def __str__(self):
        return f"{self.chef_ref_id}: {self.rating}"
