from uuid import uuid4
from django.db import models
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.gestion_cocinero.models import Dish

def uuid4_string():
    return str(uuid4())


class OperationalNotification(models.Model):
    class Category(models.TextChoices):
        ORDER = "ORDER", "Pedido"
        PAYMENT = "PAYMENT", "Pago"
        DELIVERY = "DELIVERY", "Delivery"
        INCIDENT = "INCIDENT", "Incidencia"
        INVENTORY = "INVENTORY", "Inventario"
        ADMINISTRATIVE = "ADMINISTRATIVE", "Administrativa"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    recipient = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="operational_notifications",
    )
    role_context = models.CharField(max_length=30, blank=True)
    category = models.CharField(max_length=20, choices=Category.choices)
    event_code = models.CharField(max_length=60)
    title = models.CharField(max_length=160)
    message = models.CharField(max_length=255)
    order_ref = models.CharField(max_length=64, blank=True)
    assignment_ref = models.CharField(max_length=64, blank=True)
    incident_ref = models.CharField(max_length=64, blank=True)
    action_label = models.CharField(max_length=80, blank=True)
    action_web_path = models.CharField(max_length=255, blank=True)
    action_mobile_route = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "operational_notifications"
        indexes = [
            models.Index(fields=["recipient", "is_read", "created_at"]),
            models.Index(fields=["event_code", "created_at"]),
            models.Index(fields=["order_ref", "created_at"]),
        ]

    def __str__(self):
        return f"{self.recipient_id} - {self.event_code}"


class NotificationDeviceToken(models.Model):
    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"
        WEB = "web", "Web"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="notification_device_tokens",
    )
    platform = models.CharField(max_length=20, choices=Platform.choices)
    token = models.TextField(unique=True)
    device_id = models.CharField(max_length=120, blank=True)
    device_label = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notification_device_tokens"
        indexes = [
            models.Index(fields=["user", "is_active", "platform"]),
            models.Index(fields=["device_id", "platform"]),
            models.Index(fields=["last_seen_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} - {self.platform}"


class PublicationReport(models.Model):
    REASON_CHOICES = (
        ("imagen_engañosa", "Imagen engañosa"),
        ("descripcion_falsa", "Descripción falsa"),
        ("contenido_inapropiado", "Contenido inapropiado"),
        ("precio_falso", "Precio falso"),
        ("informacion_incompleta", "Información incompleta"),
        ("otro", "Otro"),
    )

    STATUS_PENDIENTE = "pendiente"
    STATUS_REVISADO = "revisado"
    STATUS_DESCARTADO = "descartado"

    STATUS_CHOICES = (
        (STATUS_PENDIENTE, "Pendiente"),
        (STATUS_REVISADO, "Revisado"),
        (STATUS_DESCARTADO, "Descartado"),
    )

    id = models.AutoField(primary_key=True)
    publication = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="reports")
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="submitted_reports")
    reason = models.CharField(max_length=50, choices=REASON_CHOICES)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDIENTE)

    class Meta:
        db_table = "publication_reports"

    def __str__(self):
        return f"Report {self.id} for {self.publication.name} by {self.user.email}"
