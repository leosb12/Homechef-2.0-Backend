from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from django.db import transaction
from django.utils import timezone

from modules.confianza_administracion_seguridad.services import NotificationService
from modules.delivery_logistica.models import DeliveryAssignment, DeliveryLocationPing, DeliveryStatusHistory
from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import DeliveryProfile, UserProfile
from modules.pedidos_checkout_pagos.models import Order


class DeliveryAssignmentEngine:
    STRATEGY = "NEAREST_APPROVED_ACTIVE_DRIVER"
    MAX_ACTIVE_ASSIGNMENTS_PER_DRIVER = 3
    MAX_ASSIGNMENT_ATTEMPTS = 8
    ASSIGNMENT_RETRY_COOLDOWN_SECONDS = 45
    ASSIGNABLE_STATUSES = {
        DeliveryAssignment.Status.UNASSIGNED,
        DeliveryAssignment.Status.ASSIGNED,
        DeliveryAssignment.Status.AT_CHEF,
    }
    ACTIVE_DRIVER_ASSIGNMENT_STATUSES = {
        DeliveryAssignment.Status.ASSIGNED,
        DeliveryAssignment.Status.AT_CHEF,
        DeliveryAssignment.Status.PICKED_UP,
        DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT,
    }

    def __init__(self):
        self.notification_service = NotificationService()

    @transaction.atomic
    def ensure_assignment_up_to_date(
        self,
        assignment: DeliveryAssignment,
        *,
        reason: str,
        actor_role: str = "SISTEMA",
        actor_id: str = "",
        force: bool = False,
        excluded_delivery_user_ids: list[str] | None = None,
    ):
        assignment = (
            DeliveryAssignment.objects.select_for_update()
            .select_related("order", "order__client", "order__chef")
            .prefetch_related("order__address")
            .get(pk=assignment.pk)
        )
        if assignment.order.fulfillment_type != Order.FulfillmentType.DELIVERY:
            return assignment
        if assignment.order.status != Order.Status.READY_FOR_DELIVERY:
            return assignment
        if assignment.status not in self.ASSIGNABLE_STATUSES:
            return assignment

        metadata = dict(assignment.metadata or {})
        if not force and self._cooldown_active(metadata):
            return assignment
        if not force and int(metadata.get("assignment_attempt_count", 0) or 0) >= self.MAX_ASSIGNMENT_ATTEMPTS:
            return assignment

        if assignment.delivery_user_id and self._is_driver_operational(assignment.delivery_user):
            self._refresh_metadata_snapshot(
                assignment,
                metadata=metadata,
                candidates=self._build_candidate_snapshot(assignment, excluded_delivery_user_ids or []),
                reason=reason,
            )
            return assignment

        previous_status = assignment.status
        previous_user = assignment.delivery_user
        candidates = self._build_candidate_snapshot(assignment, excluded_delivery_user_ids or [])
        candidate_snapshot = self._serializable_candidates(candidates)
        selected = candidates[0] if candidates else None
        now = timezone.now()

        metadata["assignment_attempt_count"] = int(metadata.get("assignment_attempt_count", 0) or 0) + 1
        metadata["assignment_last_attempt_at"] = now.isoformat()
        metadata["assignment_strategy"] = self.STRATEGY
        metadata["candidate_snapshot"] = candidate_snapshot
        metadata["assignment_last_reason"] = reason
        metadata["assignment_retry_cooldown_seconds"] = self.ASSIGNMENT_RETRY_COOLDOWN_SECONDS
        metadata["assignment_max_attempts"] = self.MAX_ASSIGNMENT_ATTEMPTS

        update_fields = ["metadata", "updated_at"]
        if selected:
            assignment.delivery_user = selected["profile"]
            assignment.assigned_at = assignment.assigned_at or now
            assignment.status = DeliveryAssignment.Status.ASSIGNED
            metadata["assigned_delivery_user_id"] = str(selected["profile"].supabase_user_id)
            metadata["assigned_distance_meters"] = float(selected["distance_meters"])
            metadata["assigned_distance_human"] = self._distance_human(selected["distance_meters"])
            metadata["assignment_last_result"] = "assigned"
            metadata["assignment_location_source"] = selected["location_source"]
            update_fields.extend(["delivery_user", "assigned_at"])
            if previous_status != DeliveryAssignment.Status.ASSIGNED:
                update_fields.append("status")
            self._append_reassignment_history(
                metadata,
                from_user=previous_user,
                to_user=selected["profile"],
                reason=reason,
                distance_meters=selected["distance_meters"],
            )
            self._record_history(
                assignment,
                from_status=previous_status,
                to_status=DeliveryAssignment.Status.ASSIGNED,
                actor_role=actor_role,
                actor_id=actor_id,
                notes=self._assignment_note(reason, previous_user is not None),
                metadata={
                    "event_type": "assignment_selected",
                    "reason": reason,
                    "delivery_user_id": str(selected["profile"].supabase_user_id),
                    "distance_meters": float(selected["distance_meters"]),
                    "candidate_snapshot": candidate_snapshot,
                },
            )
        else:
            assignment.delivery_user = None
            assignment.status = DeliveryAssignment.Status.UNASSIGNED
            metadata["assigned_delivery_user_id"] = ""
            metadata["assigned_distance_meters"] = None
            metadata["assigned_distance_human"] = ""
            metadata["assignment_last_result"] = "no_candidate"
            metadata["assignment_location_source"] = ""
            update_fields.append("delivery_user")
            if previous_status != DeliveryAssignment.Status.UNASSIGNED:
                update_fields.append("status")
            self._append_reassignment_history(
                metadata,
                from_user=previous_user,
                to_user=None,
                reason=reason,
                distance_meters=None,
            )
            self._record_history(
                assignment,
                from_status=previous_status,
                to_status=DeliveryAssignment.Status.UNASSIGNED,
                actor_role=actor_role,
                actor_id=actor_id,
                notes="No se encontro un repartidor operativo para el pedido.",
                metadata={
                    "event_type": "assignment_unassigned",
                    "reason": reason,
                    "candidate_snapshot": candidate_snapshot,
                },
            )

        assignment.metadata = metadata
        assignment.save(update_fields=list(dict.fromkeys(update_fields)))
        assignment.refresh_from_db()
        if selected:
            self.notification_service.notify_delivery_assigned(assignment)
        return assignment

    @transaction.atomic
    def reject_and_reassign(
        self,
        assignment: DeliveryAssignment,
        *,
        delivery: UserProfile,
        actor_role: str,
        actor_id: str,
    ):
        assignment = (
            DeliveryAssignment.objects.select_for_update()
            .select_related("order", "order__client", "order__chef")
            .prefetch_related("order__address")
            .get(pk=assignment.pk)
        )
        metadata = dict(assignment.metadata or {})
        self._append_reassignment_history(
            metadata,
            from_user=assignment.delivery_user,
            to_user=None,
            reason="delivery_rejected",
            distance_meters=metadata.get("assigned_distance_meters"),
        )
        assignment.delivery_user = None
        assignment.status = DeliveryAssignment.Status.UNASSIGNED
        assignment.metadata = metadata
        assignment.save(update_fields=["delivery_user", "status", "metadata", "updated_at"])
        self._record_history(
            assignment,
            from_status=DeliveryAssignment.Status.ASSIGNED,
            to_status=DeliveryAssignment.Status.UNASSIGNED,
            actor_role=actor_role,
            actor_id=actor_id,
            notes="Repartidor rechazo la entrega antes de recogerla.",
            metadata={
                "event_type": "assignment_rejected",
                "delivery_user_id": str(delivery.supabase_user_id),
            },
        )
        return self.ensure_assignment_up_to_date(
            assignment,
            reason="delivery_rejected",
            actor_role=actor_role,
            actor_id=actor_id,
            force=True,
            excluded_delivery_user_ids=[str(delivery.supabase_user_id)],
        )

    def sync_open_assignments(self):
        assignments = (
            DeliveryAssignment.objects.filter(
                order__fulfillment_type=Order.FulfillmentType.DELIVERY,
                order__status=Order.Status.READY_FOR_DELIVERY,
                status__in=list(self.ASSIGNABLE_STATUSES),
            )
            .select_related("order", "order__client", "order__chef", "delivery_user")
            .prefetch_related("order__address")
            .order_by("-updated_at")
        )
        for assignment in assignments:
            self.ensure_assignment_up_to_date(
                assignment,
                reason="open_board_sync",
                actor_role="SISTEMA",
                actor_id="",
            )

    def _build_candidate_snapshot(self, assignment: DeliveryAssignment, excluded_delivery_user_ids: list[str]):
        chef_point = self._chef_point(assignment)
        if chef_point["lat"] is None or chef_point["lng"] is None:
            return []
        excluded = {str(value) for value in excluded_delivery_user_ids if value}
        profiles = (
            DeliveryProfile.objects.select_related("user")
            .filter(
                approval_status=DeliveryProfile.ApprovalStatus.ACTIVE,
                user__role=UserProfile.ROLE_DELIVERY,
                user__is_active=True,
            )
            .order_by("user__created_at")
        )
        candidates = []
        for profile in profiles:
            if str(profile.user.supabase_user_id) in excluded:
                continue
            active_assignments = DeliveryAssignment.objects.filter(
                delivery_user=profile.user,
                status__in=list(self.ACTIVE_DRIVER_ASSIGNMENT_STATUSES),
            ).exclude(pk=assignment.pk).count()
            if active_assignments >= self.MAX_ACTIVE_ASSIGNMENTS_PER_DRIVER:
                continue
            point = self._delivery_point(profile.user)
            if point["lat"] is None or point["lng"] is None:
                continue
            distance = self._haversine_distance_meters(
                point["lat"],
                point["lng"],
                chef_point["lat"],
                chef_point["lng"],
            )
            candidates.append(
                {
                    "profile": profile.user,
                    "delivery_user_id": str(profile.user.supabase_user_id),
                    "delivery_name": self._profile_name(profile.user),
                    "distance_meters": float(distance),
                    "distance_human": self._distance_human(distance),
                    "location_source": point["source"],
                    "active_assignments": active_assignments,
                }
            )
        candidates.sort(
            key=lambda item: (
                item["distance_meters"],
                item["active_assignments"],
                item["delivery_name"].lower(),
            )
        )
        return candidates[:10]

    def _refresh_metadata_snapshot(self, assignment: DeliveryAssignment, *, metadata: dict, candidates: list[dict], reason: str):
        metadata["assignment_strategy"] = self.STRATEGY
        metadata["candidate_snapshot"] = self._serializable_candidates(candidates)
        metadata["assignment_last_reason"] = reason
        assignment.metadata = metadata
        assignment.save(update_fields=["metadata", "updated_at"])

    def _serializable_candidates(self, candidates: list[dict]):
        return [
            {
                "delivery_user_id": item["delivery_user_id"],
                "delivery_name": item["delivery_name"],
                "distance_meters": float(item["distance_meters"]),
                "distance_human": item["distance_human"],
                "location_source": item["location_source"],
                "active_assignments": int(item["active_assignments"]),
            }
            for item in candidates
        ]

    def _append_reassignment_history(self, metadata: dict, *, from_user: UserProfile | None, to_user: UserProfile | None, reason: str, distance_meters):
        history = list(metadata.get("reassignment_history") or [])
        history.append(
            {
                "at": timezone.now().isoformat(),
                "reason": reason,
                "from_delivery_user_id": str(from_user.supabase_user_id) if from_user else "",
                "from_delivery_name": self._profile_name(from_user),
                "to_delivery_user_id": str(to_user.supabase_user_id) if to_user else "",
                "to_delivery_name": self._profile_name(to_user),
                "distance_meters": float(distance_meters) if distance_meters is not None else None,
                "distance_human": self._distance_human(distance_meters) if distance_meters is not None else "",
            }
        )
        metadata["reassignment_history"] = history[-12:]

    def _record_history(self, assignment: DeliveryAssignment, *, from_status: str, to_status: str, actor_role: str, actor_id: str, notes: str, metadata: dict):
        DeliveryStatusHistory.objects.create(
            assignment=assignment,
            from_status=from_status,
            to_status=to_status,
            actor_role=actor_role,
            actor_id=str(actor_id or ""),
            notes=notes,
            metadata=metadata,
        )

    def _assignment_note(self, reason: str, reassigned: bool):
        if reason == "delivery_rejected":
            return "Entrega reasignada luego del rechazo del repartidor."
        if reassigned:
            return "Entrega reasignada por politica automatica de cercania."
        return "Entrega asignada automaticamente por cercania."

    def _cooldown_active(self, metadata: dict):
        raw_value = metadata.get("assignment_last_attempt_at")
        if not raw_value:
            return False
        parsed = self._parse_datetime(raw_value)
        if not parsed:
            return False
        return (timezone.now() - parsed).total_seconds() < self.ASSIGNMENT_RETRY_COOLDOWN_SECONDS

    def _parse_datetime(self, value: str):
        try:
            return timezone.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError, AttributeError):
            return None

    def _is_driver_operational(self, profile: UserProfile | None):
        if not profile or not profile.is_active:
            return False
        delivery_profile = getattr(profile, "delivery_profile", None)
        return bool(
            delivery_profile
            and delivery_profile.approval_status == DeliveryProfile.ApprovalStatus.ACTIVE
        )

    def _delivery_point(self, delivery_user: UserProfile):
        latest_ping = (
            DeliveryLocationPing.objects.filter(assignment__delivery_user=delivery_user)
            .order_by("-recorded_at", "-created_at")
            .first()
        )
        if latest_ping:
            return {
                "lat": float(latest_ping.latitude),
                "lng": float(latest_ping.longitude),
                "source": "LAST_PING",
            }
        return {
            "lat": delivery_user.location_latitude,
            "lng": delivery_user.location_longitude,
            "source": "PROFILE_LOCATION",
        }

    def _chef_point(self, assignment: DeliveryAssignment):
        profile = ChefProfile.objects.filter(user=assignment.order.chef).first()
        return {
            "lat": profile.location_latitude if profile else None,
            "lng": profile.location_longitude if profile else None,
        }

    def _profile_name(self, profile: UserProfile | None):
        if not profile:
            return ""
        name = f"{profile.first_name} {profile.last_name}".strip()
        return name or profile.full_name or profile.email

    def _distance_human(self, value):
        if value is None:
            return ""
        distance = float(value)
        if distance >= 1000:
            return f"{distance / 1000:.1f} km"
        return f"{int(round(distance))} m"

    def _haversine_distance_meters(self, lat1: float, lng1: float, lat2: float, lng2: float):
        radius = 6371000
        dlat = radians(lat2 - lat1)
        dlng = radians(lng2 - lng1)
        origin_lat = radians(lat1)
        target_lat = radians(lat2)
        formula = sin(dlat / 2) ** 2 + cos(origin_lat) * cos(target_lat) * sin(dlng / 2) ** 2
        return 2 * radius * asin(sqrt(formula))
