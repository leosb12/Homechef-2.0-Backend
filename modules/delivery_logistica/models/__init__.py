from uuid import uuid4

from django.db import models

from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import Order


def uuid4_string():
    return str(uuid4())


class DeliveryAssignment(models.Model):
    class Status(models.TextChoices):
        UNASSIGNED = "UNASSIGNED", "Sin asignar"
        ASSIGNED = "ASSIGNED", "Asignado"
        EN_ROUTE_TO_CHEF = "EN_ROUTE_TO_CHEF", "En ruta al cocinero"
        AT_CHEF = "AT_CHEF", "En punto de recogida"
        PICKED_UP = "PICKED_UP", "Recogido"
        EN_ROUTE_TO_CLIENT = "EN_ROUTE_TO_CLIENT", "En ruta al cliente"
        DELIVERED = "DELIVERED", "Entregado"
        FAILED = "FAILED", "Fallido"
        CANCELLED = "CANCELLED", "Cancelado"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="delivery_assignment",
        limit_choices_to={"fulfillment_type": Order.FulfillmentType.DELIVERY},
    )
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.UNASSIGNED)
    delivery_user = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        related_name="delivery_assignments",
        null=True,
        blank=True,
        limit_choices_to={"role": UserProfile.ROLE_DELIVERY},
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "delivery_assignments"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["delivery_user", "status"]),
            models.Index(fields=["assigned_at"]),
        ]

    def __str__(self):
        return f"{self.order_id} - {self.status}"


class DeliveryAssignmentOffer(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        ACCEPTED = "ACCEPTED", "Aceptada"
        REJECTED = "REJECTED", "Rechazada"
        EXPIRED = "EXPIRED", "Expirada"
        CANCELLED = "CANCELLED", "Cancelada"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    assignment = models.ForeignKey(
        DeliveryAssignment,
        on_delete=models.CASCADE,
        related_name="offers",
    )
    delivery_user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="delivery_assignment_offers",
        limit_choices_to={"role": UserProfile.ROLE_DELIVERY},
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    offered_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    response_reason = models.CharField(max_length=120, blank=True)
    attempt_number = models.PositiveIntegerField(default=1)
    selection_distance_meters = models.FloatField(default=0)
    selection_snapshot = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "delivery_assignment_offers"
        indexes = [
            models.Index(fields=["assignment", "status"]),
            models.Index(fields=["delivery_user", "status"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["offered_at"]),
        ]

    def __str__(self):
        return f"{self.assignment_id} -> {self.delivery_user_id} ({self.status})"


class DeliveryStatusHistory(models.Model):
    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    assignment = models.ForeignKey(
        DeliveryAssignment,
        on_delete=models.CASCADE,
        related_name="status_history",
    )
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30)
    actor_role = models.CharField(max_length=30)
    actor_id = models.CharField(max_length=64, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "delivery_status_history"
        indexes = [
            models.Index(fields=["assignment", "occurred_at"]),
            models.Index(fields=["to_status", "occurred_at"]),
        ]

    def __str__(self):
        return f"{self.assignment_id}: {self.from_status} -> {self.to_status}"


class DeliveryLocationPing(models.Model):
    class Source(models.TextChoices):
        SYSTEM = "SYSTEM", "Sistema"
        DELIVERY_APP = "DELIVERY_APP", "App delivery"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    assignment = models.ForeignKey(
        DeliveryAssignment,
        on_delete=models.CASCADE,
        related_name="location_pings",
    )
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.SYSTEM)
    latitude = models.FloatField()
    longitude = models.FloatField()
    accuracy_meters = models.FloatField(null=True, blank=True)
    speed_mps = models.FloatField(null=True, blank=True)
    heading_degrees = models.FloatField(null=True, blank=True)
    recorded_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "delivery_location_pings"
        indexes = [
            models.Index(fields=["assignment", "recorded_at"]),
            models.Index(fields=["latitude", "longitude"]),
        ]

    def __str__(self):
        return f"{self.assignment_id} @ {self.recorded_at.isoformat()}"


class DeliveryIncident(models.Model):
    class Code(models.TextChoices):
        DELAY = "DELAY", "Retraso"
        WRONG_ADDRESS = "WRONG_ADDRESS", "Direccion incorrecta"
        CLIENT_ABSENT = "CLIENT_ABSENT", "Cliente ausente"
        ORDER_DAMAGED = "ORDER_DAMAGED", "Pedido danado"
        CANCELLATION_REQUEST = "CANCELLATION_REQUEST", "Solicitud de cancelacion"
        CANNOT_COMPLETE = "CANNOT_COMPLETE", "No se puede completar la entrega"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Abierta"
        RESOLVED = "RESOLVED", "Resuelta"
        CANCELLED = "CANCELLED", "Cancelada"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    assignment = models.ForeignKey(
        DeliveryAssignment,
        on_delete=models.CASCADE,
        related_name="incidents",
    )
    code = models.CharField(max_length=60, choices=Code.choices)
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    reported_by_role = models.CharField(max_length=30)
    reported_by_user = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        related_name="reported_delivery_incidents",
        null=True,
        blank=True,
    )
    evidence_urls = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    resolved_by_role = models.CharField(max_length=30, blank=True)
    resolved_by_user = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        related_name="resolved_delivery_incidents",
        null=True,
        blank=True,
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "delivery_incidents"
        indexes = [
            models.Index(fields=["assignment", "status"]),
            models.Index(fields=["code", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.assignment_id} - {self.code} - {self.status}"


class DeliveryRouteSnapshot(models.Model):
    class RouteKind(models.TextChoices):
        TO_CHEF = "TO_CHEF", "Hacia cocinero"
        TO_CLIENT = "TO_CLIENT", "Hacia cliente"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    assignment = models.ForeignKey(
        DeliveryAssignment,
        on_delete=models.CASCADE,
        related_name="route_snapshots",
    )
    route_kind = models.CharField(max_length=20, choices=RouteKind.choices)
    provider = models.CharField(max_length=40, default="OSM_FALLBACK")
    is_current = models.BooleanField(default=True)
    start_latitude = models.FloatField()
    start_longitude = models.FloatField()
    end_latitude = models.FloatField()
    end_longitude = models.FloatField()
    distance_meters = models.FloatField(default=0)
    duration_seconds = models.PositiveIntegerField(default=0)
    polyline = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "delivery_route_snapshots"
        indexes = [
            models.Index(fields=["assignment", "is_current"]),
            models.Index(fields=["route_kind", "created_at"]),
        ]

    def __str__(self):
        return f"{self.assignment_id} - {self.route_kind} - {self.provider}"
