from django.db import models


class UserProfile(models.Model):
    ROLE_CLIENT = "CLIENTE"
    ROLE_CHEF = "COCINERO"
    ROLE_ADMIN = "ADMINISTRADOR"
    ROLE_DELIVERY = "REPARTIDOR"

    ROLE_CHOICES = (
        (ROLE_CLIENT, "Cliente"),
        (ROLE_CHEF, "Cocinero"),
        (ROLE_ADMIN, "Administrador"),
        (ROLE_DELIVERY, "Repartidor"),
    )

    supabase_user_id = models.UUIDField(unique=True, db_index=True)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    full_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(max_length=1000, blank=True)
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default=ROLE_CLIENT)
    phone = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    accept_terms = models.BooleanField(default=False)
    notify_gmail = models.BooleanField(default=True)
    notify_push = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    legacy_mongo_id = models.CharField(max_length=128, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_profiles"
        indexes = [
            models.Index(fields=["role"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self):
        return self.email


class AuditEvent(models.Model):
    event = models.CharField(max_length=120)
    details = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField()

    class Meta:
        db_table = "audit_events"
        indexes = [
            models.Index(fields=["event"]),
            models.Index(fields=["at"]),
        ]

    def __str__(self):
        return f"{self.event} at {self.at}"
