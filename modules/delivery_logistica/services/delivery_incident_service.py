from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from modules.confianza_administracion_seguridad.services import NotificationService
from modules.delivery_logistica.models import DeliveryAssignment, DeliveryIncident
from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import Order, OrderTimelineEvent


class DeliveryIncidentError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class DeliveryIncidentService:
    INCIDENT_CATALOG = {
        DeliveryIncident.Code.DELAY: {
            "title": "Retraso en entrega",
            "blocking": False,
        },
        DeliveryIncident.Code.WRONG_ADDRESS: {
            "title": "Direccion incorrecta",
            "blocking": True,
        },
        DeliveryIncident.Code.CLIENT_ABSENT: {
            "title": "Cliente ausente",
            "blocking": True,
        },
        DeliveryIncident.Code.ORDER_DAMAGED: {
            "title": "Pedido danado",
            "blocking": True,
        },
        DeliveryIncident.Code.CANCELLATION_REQUEST: {
            "title": "Solicitud de cancelacion",
            "blocking": True,
        },
        DeliveryIncident.Code.CANNOT_COMPLETE: {
            "title": "No se puede completar la entrega",
            "blocking": True,
        },
    }

    def __init__(self):
        self.notification_service = NotificationService()

    def list_for_client(self, user_id: str, order_id: str):
        client = self._require_profile(user_id, UserProfile.ROLE_CLIENT, "client_not_found", "Perfil de cliente no encontrado.")
        assignment = self._get_assignment_for_client(client, order_id)
        return self._serialize_incident_collection(assignment, viewer_role="CLIENTE")

    def list_for_chef(self, user_id: str, order_id: str):
        chef = self._require_profile(user_id, UserProfile.ROLE_CHEF, "chef_not_found", "Perfil de cocinero no encontrado.")
        assignment = self._get_assignment_for_chef(chef, order_id)
        return self._serialize_incident_collection(assignment, viewer_role="COCINERO")

    def list_for_delivery(self, user_id: str, assignment_id: str):
        delivery = self._require_profile(user_id, UserProfile.ROLE_DELIVERY, "delivery_not_found", "Perfil de repartidor no encontrado.")
        assignment = self._get_assignment_for_delivery(delivery, assignment_id)
        return self._serialize_incident_collection(assignment, viewer_role="REPARTIDOR")

    @transaction.atomic
    def create_for_client(self, user_id: str, order_id: str, payload: dict):
        client = self._require_profile(user_id, UserProfile.ROLE_CLIENT, "client_not_found", "Perfil de cliente no encontrado.")
        assignment = self._get_assignment_for_client(client, order_id, lock=True)
        self._assert_incident_report_allowed(assignment, reporter_role="CLIENTE")
        incident = self._create_incident(assignment, client, "CLIENTE", payload)
        return {
            "incident": self._serialize_incident(incident, viewer_role="CLIENTE"),
            "incidents": self._serialize_incident_collection(assignment, viewer_role="CLIENTE"),
        }

    @transaction.atomic
    def create_for_delivery(self, user_id: str, assignment_id: str, payload: dict):
        delivery = self._require_profile(user_id, UserProfile.ROLE_DELIVERY, "delivery_not_found", "Perfil de repartidor no encontrado.")
        assignment = self._get_assignment_for_delivery(delivery, assignment_id, lock=True)
        self._assert_incident_report_allowed(assignment, reporter_role="REPARTIDOR")
        incident = self._create_incident(assignment, delivery, "REPARTIDOR", payload)
        if assignment.delivery_user_id:
            from modules.delivery_logistica.realtime import publish_assignment_snapshot_for_delivery

            publish_assignment_snapshot_for_delivery(
                assignment.id,
                str(assignment.delivery_user.supabase_user_id),
            )
        return {
            "incident": self._serialize_incident(incident, viewer_role="REPARTIDOR"),
            "incidents": self._serialize_incident_collection(assignment, viewer_role="REPARTIDOR"),
        }

    @transaction.atomic
    def resolve_for_chef(self, user_id: str, order_id: str, incident_id: str, payload: dict):
        chef = self._require_profile(user_id, UserProfile.ROLE_CHEF, "chef_not_found", "Perfil de cocinero no encontrado.")
        assignment = self._get_assignment_for_chef(chef, order_id, lock=True)
        incident = self._get_incident(assignment, incident_id)
        self._assert_resolution_allowed(incident, resolver_role="COCINERO")
        self._resolve_incident(incident, chef, "COCINERO", payload)
        return {
            "incident": self._serialize_incident(incident, viewer_role="COCINERO"),
            "incidents": self._serialize_incident_collection(assignment, viewer_role="COCINERO"),
        }

    @transaction.atomic
    def resolve_for_delivery(self, user_id: str, assignment_id: str, incident_id: str, payload: dict):
        delivery = self._require_profile(user_id, UserProfile.ROLE_DELIVERY, "delivery_not_found", "Perfil de repartidor no encontrado.")
        assignment = self._get_assignment_for_delivery(delivery, assignment_id, lock=True)
        incident = self._get_incident(assignment, incident_id)
        self._assert_resolution_allowed(incident, resolver_role="REPARTIDOR")
        self._resolve_incident(incident, delivery, "REPARTIDOR", payload)
        if assignment.delivery_user_id:
            from modules.delivery_logistica.realtime import publish_assignment_snapshot_for_delivery

            publish_assignment_snapshot_for_delivery(
                assignment.id,
                str(assignment.delivery_user.supabase_user_id),
            )
        return {
            "incident": self._serialize_incident(incident, viewer_role="REPARTIDOR"),
            "incidents": self._serialize_incident_collection(assignment, viewer_role="REPARTIDOR"),
        }

    def incident_summary(self, assignment: DeliveryAssignment | None, viewer_role: str):
        if not assignment:
            return {
                "open_count": 0,
                "blocking_open_count": 0,
                "delivery_blocked": False,
                "items": [],
            }
        return self._serialize_incident_collection(assignment, viewer_role=viewer_role)

    def has_blocking_open_incident(self, assignment: DeliveryAssignment | None):
        if not assignment:
            return False
        return any(self._is_blocking(incident.code) for incident in assignment.incidents.filter(status=DeliveryIncident.Status.OPEN))

    def _create_incident(self, assignment: DeliveryAssignment, reporter: UserProfile, reporter_role: str, payload: dict):
        code = payload["code"]
        catalog_item = self.INCIDENT_CATALOG[code]
        incident = DeliveryIncident.objects.create(
            assignment=assignment,
            code=code,
            title=catalog_item["title"],
            description=str(payload.get("description") or "").strip(),
            reported_by_role=reporter_role,
            reported_by_user=reporter,
            evidence_urls=payload.get("evidence_urls") or [],
            metadata={
                "blocking": catalog_item["blocking"],
                "evidence_note": str(payload.get("evidence_note") or "").strip(),
            },
        )
        self._append_order_timeline(
            assignment.order,
            "DELIVERY_INCIDENT_OPENED",
            f"Incidencia de entrega: {incident.title}",
            reporter_role,
            str(reporter.supabase_user_id),
            {
                "assignment_id": assignment.id,
                "incident_id": incident.id,
                "incident_code": incident.code,
                "blocking": catalog_item["blocking"],
            },
        )
        self.notification_service.notify_incident_created(incident)
        return incident

    def _resolve_incident(self, incident: DeliveryIncident, resolver: UserProfile, resolver_role: str, payload: dict):
        incident.status = DeliveryIncident.Status.RESOLVED
        incident.resolved_at = timezone.now()
        incident.resolution_notes = str(payload.get("resolution_notes") or "").strip()
        incident.resolved_by_role = resolver_role
        incident.resolved_by_user = resolver
        incident.save(
            update_fields=[
                "status",
                "resolved_at",
                "resolution_notes",
                "resolved_by_role",
                "resolved_by_user",
                "updated_at",
            ]
        )
        self._append_order_timeline(
            incident.assignment.order,
            "DELIVERY_INCIDENT_RESOLVED",
            f"Incidencia resuelta: {incident.title}",
            resolver_role,
            str(resolver.supabase_user_id),
            {
                "assignment_id": incident.assignment_id,
                "incident_id": incident.id,
                "incident_code": incident.code,
            },
        )

    def _serialize_incident_collection(self, assignment: DeliveryAssignment, viewer_role: str):
        incidents = list(assignment.incidents.all().order_by("-created_at"))
        open_count = sum(1 for incident in incidents if incident.status == DeliveryIncident.Status.OPEN)
        blocking_open_count = sum(
            1
            for incident in incidents
            if incident.status == DeliveryIncident.Status.OPEN and self._is_blocking(incident.code)
        )
        return {
            "open_count": open_count,
            "blocking_open_count": blocking_open_count,
            "delivery_blocked": blocking_open_count > 0,
            "items": [self._serialize_incident(incident, viewer_role=viewer_role) for incident in incidents],
        }

    def _serialize_incident(self, incident: DeliveryIncident, viewer_role: str):
        return {
            "id": incident.id,
            "code": incident.code,
            "title": incident.title,
            "description": incident.description,
            "status": incident.status,
            "status_label": self._status_label(incident.status),
            "blocking": self._is_blocking(incident.code),
            "reported_by_role": incident.reported_by_role,
            "reported_by_name": self._profile_name(incident.reported_by_user),
            "evidence_urls": incident.evidence_urls or [],
            "evidence_note": (incident.metadata or {}).get("evidence_note", ""),
            "created_at": incident.created_at.isoformat(),
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            "resolved_by_role": incident.resolved_by_role,
            "resolved_by_name": self._profile_name(incident.resolved_by_user),
            "resolution_notes": incident.resolution_notes,
            "can_resolve": incident.status == DeliveryIncident.Status.OPEN and viewer_role in {"COCINERO", "REPARTIDOR"},
        }

    def _get_assignment_for_client(self, client: UserProfile, order_id: str, lock: bool = False):
        queryset = DeliveryAssignment.objects.filter(order__id=str(order_id), order__client=client)
        if lock:
            queryset = queryset.select_for_update()
        assignment = (
            queryset.select_related("order", "order__client", "order__chef")
            .prefetch_related("incidents")
            .first()
        )
        if not assignment:
            raise DeliveryIncidentError("Entrega no encontrada para el cliente.", "assignment_not_found")
        return assignment

    def _get_assignment_for_chef(self, chef: UserProfile, order_id: str, lock: bool = False):
        queryset = DeliveryAssignment.objects.filter(order__id=str(order_id), order__chef=chef)
        if lock:
            queryset = queryset.select_for_update()
        assignment = (
            queryset.select_related("order", "order__client", "order__chef")
            .prefetch_related("incidents")
            .first()
        )
        if not assignment:
            raise DeliveryIncidentError("Entrega no encontrada para el cocinero.", "assignment_not_found")
        return assignment

    def _get_assignment_for_delivery(self, delivery: UserProfile, assignment_id: str, lock: bool = False):
        queryset = DeliveryAssignment.objects.filter(id=str(assignment_id), delivery_user=delivery)
        if lock:
            queryset = queryset.select_for_update()
        assignment = (
            queryset.select_related("order", "order__client", "order__chef")
            .prefetch_related("incidents")
            .first()
        )
        if not assignment:
            raise DeliveryIncidentError("Entrega no encontrada para el repartidor.", "assignment_not_found")
        return assignment

    def _get_incident(self, assignment: DeliveryAssignment, incident_id: str):
        incident = assignment.incidents.filter(id=str(incident_id)).first()
        if not incident:
            raise DeliveryIncidentError("Incidencia no encontrada para la entrega.", "incident_not_found")
        return incident

    def _assert_incident_report_allowed(self, assignment: DeliveryAssignment, reporter_role: str):
        if assignment.order.fulfillment_type != Order.FulfillmentType.DELIVERY:
            raise DeliveryIncidentError("Solo los pedidos delivery admiten incidencias de entrega.", "invalid_fulfillment_type")
        if assignment.status in {DeliveryAssignment.Status.CANCELLED}:
            raise DeliveryIncidentError("La entrega ya no admite incidencias en su estado actual.", "incident_transition_not_allowed")
        if reporter_role == "CLIENTE" and assignment.status == DeliveryAssignment.Status.UNASSIGNED:
            raise DeliveryIncidentError("La entrega aun no entro en una etapa reportable para el cliente.", "incident_transition_not_allowed")
        if reporter_role == "REPARTIDOR" and assignment.status not in {
            DeliveryAssignment.Status.ASSIGNED,
            DeliveryAssignment.Status.AT_CHEF,
            DeliveryAssignment.Status.PICKED_UP,
            DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT,
        }:
            raise DeliveryIncidentError(
                "El repartidor solo puede registrar incidencias en entregas activas asignadas a su usuario.",
                "incident_transition_not_allowed",
            )

    def _assert_resolution_allowed(self, incident: DeliveryIncident, resolver_role: str):
        if incident.status != DeliveryIncident.Status.OPEN:
            raise DeliveryIncidentError("La incidencia ya no esta abierta para resolucion.", "incident_already_closed")
        if resolver_role not in {"COCINERO", "REPARTIDOR"}:
            raise DeliveryIncidentError("Tu rol no puede resolver incidencias de entrega.", "incident_resolution_forbidden")

    def _append_order_timeline(self, order: Order, event_code: str, event_label: str, actor_role: str, actor_id: str, metadata: dict):
        OrderTimelineEvent.objects.create(
            order=order,
            event_code=event_code,
            event_label=event_label,
            actor_role=actor_role,
            actor_id=str(actor_id or ""),
            metadata=metadata,
        )

    def _require_profile(self, user_id: str, role: str, code: str, message: str):
        parsed = self._parse_uuid(user_id)
        profile = UserProfile.objects.filter(supabase_user_id=parsed, role=role).first() if parsed else None
        if not profile:
            raise DeliveryIncidentError(message, code)
        return profile

    def _status_label(self, value: str):
        labels = {
            DeliveryIncident.Status.OPEN: "Abierta",
            DeliveryIncident.Status.RESOLVED: "Resuelta",
            DeliveryIncident.Status.CANCELLED: "Cancelada",
        }
        return labels.get(value, value)

    def _is_blocking(self, code: str):
        return bool(self.INCIDENT_CATALOG.get(code, {}).get("blocking"))

    def _profile_name(self, profile: UserProfile | None):
        if not profile:
            return ""
        if profile.role == UserProfile.ROLE_CHEF:
            chef_profile = ChefProfile.objects.filter(user=profile).first()
            if chef_profile and chef_profile.business_name:
                return chef_profile.business_name
        name = f"{profile.first_name} {profile.last_name}".strip()
        return name or profile.full_name or profile.email

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None
