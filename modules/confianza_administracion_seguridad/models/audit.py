from django.db import models


class AuditLog(models.Model):
    class Category(models.TextChoices):
        USERS = "users", "Usuarios"
        CHEFS = "chefs", "Cocineros"
        RIDERS = "riders", "Repartidores"
        ORDERS = "orders", "Pedidos"
        PUBLICATIONS = "publications", "Publicaciones"
        PAYMENTS = "payments", "Pagos"
        SECURITY = "security", "Seguridad"
        ADMIN = "admin", "Administracion"
        NOTIFICATIONS = "notifications", "Notificaciones"
        SYSTEM = "system", "Sistema"

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    id = models.BigAutoField(primary_key=True)
    event_type = models.CharField(max_length=120, db_index=True)
    event_category = models.CharField(max_length=40, choices=Category.choices, db_index=True)
    action = models.CharField(max_length=40, db_index=True)
    entity_type = models.CharField(max_length=80, db_index=True)
    entity_id = models.CharField(max_length=128, blank=True, db_index=True)
    actor_user_id = models.CharField(max_length=128, blank=True, db_index=True)
    actor_role = models.CharField(max_length=40, blank=True)
    actor_name = models.CharField(max_length=255, blank=True)
    target_user_id = models.CharField(max_length=128, blank=True)
    target_role = models.CharField(max_length=40, blank=True)
    description = models.TextField(blank=True)
    old_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    request_id = models.CharField(max_length=128, blank=True, db_index=True)
    source = models.CharField(max_length=60, default="backend", db_index=True)
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.INFO, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUCCESS, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_logs"
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["event_category"]),
            models.Index(fields=["action"]),
            models.Index(fields=["actor_user_id"]),
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["severity"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.event_type} - {self.entity_type}:{self.entity_id}"
