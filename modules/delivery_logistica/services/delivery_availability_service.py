from __future__ import annotations

from uuid import UUID

from django.db import transaction
from django.utils import timezone

from modules.delivery_logistica.models import DeliveryAssignment
from modules.gestion_usuarios_acceso_suscripcion.models import (
    DeliveryAvailabilityHistory,
    DeliveryProfile,
    UserProfile,
)


class DeliveryAvailabilityError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class DeliveryAvailabilityService:
    CAPACITY_LIMIT = 3
    ACTIVE_ASSIGNMENT_STATUSES = {
        DeliveryAssignment.Status.ASSIGNED,
        DeliveryAssignment.Status.AT_CHEF,
        DeliveryAssignment.Status.PICKED_UP,
        DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT,
    }

    @classmethod
    def resolve_effective_status(cls, manual_status: str, active_assignments_count: int):
        if manual_status == DeliveryProfile.AvailabilityManualStatus.OFF_DUTY:
            return DeliveryProfile.AvailabilityEffectiveStatus.OFF_DUTY
        if int(active_assignments_count or 0) >= cls.CAPACITY_LIMIT:
            return DeliveryProfile.AvailabilityEffectiveStatus.BUSY
        return DeliveryProfile.AvailabilityEffectiveStatus.AVAILABLE

    def get_status(self, user_id: str):
        profile = self._get_delivery_profile(user_id)
        profile = self._sync_profile(profile, reason=DeliveryAvailabilityHistory.Reason.SYNC)
        return self._serialize(profile)

    @transaction.atomic
    def update_manual_status(self, user_id: str, manual_status: str):
        profile = self._get_delivery_profile(user_id, lock=True)
        if manual_status not in {
            DeliveryProfile.AvailabilityManualStatus.AVAILABLE,
            DeliveryProfile.AvailabilityManualStatus.OFF_DUTY,
        }:
            raise DeliveryAvailabilityError(
                "Estado manual de disponibilidad invalido.",
                "availability_status_invalid",
            )
        previous_manual_status = profile.availability_manual_status
        profile.availability_manual_status = manual_status
        reason = (
            DeliveryAvailabilityHistory.Reason.MANUAL_AVAILABLE
            if manual_status == DeliveryProfile.AvailabilityManualStatus.AVAILABLE
            else DeliveryAvailabilityHistory.Reason.MANUAL_OFF_DUTY
        )
        force_history = previous_manual_status != manual_status
        profile = self._sync_profile(profile, reason=reason, force_history=force_history)
        from modules.delivery_logistica.realtime import publish_delivery_dashboard_refresh

        publish_delivery_dashboard_refresh(
            delivery_user_id=str(profile.user.supabase_user_id),
            reason="availability_updated",
        )
        return self._serialize(profile)

    @transaction.atomic
    def sync_for_user(self, delivery_user: UserProfile | str):
        profile = self._get_delivery_profile(
            str(delivery_user.supabase_user_id) if isinstance(delivery_user, UserProfile) else str(delivery_user),
            lock=True,
            required=False,
        )
        if not profile:
            return None
        return self._sync_profile(profile)

    def _serialize(self, profile: DeliveryProfile):
        active_assignments_count = self._active_assignments_count(profile.user)
        return {
            "manual_status": profile.availability_manual_status,
            "effective_status": profile.availability_effective_status,
            "active_assignments_count": active_assignments_count,
            "capacity_limit": self.CAPACITY_LIMIT,
            "status_changed_at": profile.availability_status_changed_at.isoformat()
            if profile.availability_status_changed_at
            else None,
            "delivery_user": {
                "id": str(profile.user.supabase_user_id),
                "name": self._profile_name(profile.user),
            },
        }

    def _sync_profile(
        self,
        profile: DeliveryProfile,
        *,
        reason: str | None = None,
        force_history: bool = False,
    ):
        active_assignments_count = self._active_assignments_count(profile.user)
        expected_effective_status = self.resolve_effective_status(
            profile.availability_manual_status,
            active_assignments_count,
        )
        previous_manual_status = profile.availability_manual_status or ""
        previous_effective_status = profile.availability_effective_status or ""
        next_reason = reason or DeliveryAvailabilityHistory.Reason.SYNC
        if (
            previous_effective_status == DeliveryProfile.AvailabilityEffectiveStatus.BUSY
            and expected_effective_status == DeliveryProfile.AvailabilityEffectiveStatus.AVAILABLE
        ):
            next_reason = DeliveryAvailabilityHistory.Reason.AUTO_CAPACITY_RELEASED
        elif (
            previous_effective_status != DeliveryProfile.AvailabilityEffectiveStatus.BUSY
            and expected_effective_status == DeliveryProfile.AvailabilityEffectiveStatus.BUSY
        ):
            next_reason = DeliveryAvailabilityHistory.Reason.AUTO_CAPACITY_REACHED

        changed = previous_effective_status != expected_effective_status
        if force_history or changed:
            profile.availability_status_changed_at = timezone.now()
        if force_history or changed:
            update_fields = ["availability_status_changed_at", "updated_at"]
            if force_history:
                update_fields.append("availability_manual_status")
            if changed:
                profile.availability_effective_status = expected_effective_status
                update_fields.append("availability_effective_status")
            profile.save(update_fields=update_fields)

        if force_history or changed:
            DeliveryAvailabilityHistory.objects.create(
                delivery_profile=profile,
                previous_manual_status=previous_manual_status,
                next_manual_status=profile.availability_manual_status,
                previous_effective_status=previous_effective_status,
                next_effective_status=expected_effective_status,
                active_assignments_count=active_assignments_count,
                reason=next_reason,
                metadata={"capacity_limit": self.CAPACITY_LIMIT},
            )
        return profile

    def _active_assignments_count(self, delivery_user: UserProfile):
        return DeliveryAssignment.objects.filter(
            delivery_user=delivery_user,
            status__in=list(self.ACTIVE_ASSIGNMENT_STATUSES),
        ).count()

    def _get_delivery_profile(self, user_id: str, lock: bool = False, required: bool = True):
        delivery = self._require_delivery(user_id)
        queryset = DeliveryProfile.objects.select_related("user").filter(user=delivery)
        if lock:
            queryset = queryset.select_for_update()
        profile = queryset.first()
        if not profile and required:
            raise DeliveryAvailabilityError(
                "Perfil operativo de delivery no encontrado.",
                "delivery_profile_not_found",
            )
        return profile

    def _require_delivery(self, user_id: str):
        parsed = self._parse_uuid(user_id)
        profile = (
            UserProfile.objects.filter(
                supabase_user_id=parsed,
                role=UserProfile.ROLE_DELIVERY,
            ).first()
            if parsed
            else None
        )
        if not profile:
            raise DeliveryAvailabilityError(
                "Perfil de repartidor no encontrado.",
                "delivery_not_found",
            )
        return profile

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _profile_name(self, profile: UserProfile | None):
        if not profile:
            return ""
        name = f"{profile.first_name} {profile.last_name}".strip()
        return name or profile.full_name or profile.email
