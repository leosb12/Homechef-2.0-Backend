from uuid import UUID

from modules.delivery_logistica.models import DeliveryAssignment
from modules.confianza_administracion_seguridad.services.audit_service import AuditService
from modules.gestion_usuarios_acceso_suscripcion.models import DeliveryProfile, UserProfile


class DeliveryDriverAdminError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class DeliveryDriverAdminService:
    def list_drivers(self, *, status_filter: str = ""):
        queryset = (
            DeliveryProfile.objects.select_related("user")
            .all()
            .order_by("approval_status", "-created_at")
        )
        if status_filter:
            queryset = queryset.filter(approval_status=status_filter)
        items = [self._serialize(profile) for profile in queryset]
        return {
            "items": items,
            "summary": {
                "total": len(items),
                "recien_registrado": sum(1 for item in items if item["approval_status"] == "recien_registrado"),
                "activo": sum(1 for item in items if item["approval_status"] == "activo"),
                "suspendido": sum(1 for item in items if item["approval_status"] == "suspendido"),
            },
        }

    def update_status(self, actor_user_id: str, delivery_user_id: str, next_status: str, request=None):
        actor = self._find_user(actor_user_id)
        if not actor or actor.role != UserProfile.ROLE_ADMIN:
            raise DeliveryDriverAdminError("admin_required", "Solo un administrador puede gestionar repartidores.")

        if next_status not in {
            DeliveryProfile.ApprovalStatus.ACTIVE,
            DeliveryProfile.ApprovalStatus.SUSPENDED,
        }:
            raise DeliveryDriverAdminError("invalid_status", "Estado de repartidor invalido.")

        profile = self._find_delivery_profile(delivery_user_id)
        old_values = {"approval_status": profile.approval_status, "status_notes": profile.status_notes}
        profile.approval_status = next_status
        profile.status_notes = (
            "Repartidor habilitado por administracion."
            if next_status == DeliveryProfile.ApprovalStatus.ACTIVE
            else "Repartidor suspendido por administracion."
        )
        profile.save(update_fields=["approval_status", "status_notes", "updated_at"])
        AuditService().log_event(
            event_type="RIDER_STATUS_CHANGED",
            event_category="riders",
            action="approved" if next_status == DeliveryProfile.ApprovalStatus.ACTIVE else "blocked",
            entity_type="rider_profile",
            entity_id=str(profile.id),
            actor=actor,
            target_user_id=str(profile.user.supabase_user_id),
            target_role=profile.user.role,
            description=f"Administrador cambio estado del repartidor a {next_status}.",
            old_values=old_values,
            new_values={"approval_status": profile.approval_status, "status_notes": profile.status_notes},
            request=request,
            severity="warning" if next_status == DeliveryProfile.ApprovalStatus.SUSPENDED else "info",
        )
        return {"item": self._serialize(profile)}

    def _find_delivery_profile(self, user_id: str):
        try:
            parsed = UUID(str(user_id))
        except (TypeError, ValueError):
            raise DeliveryDriverAdminError("delivery_not_found", "Repartidor no encontrado.")
        profile = DeliveryProfile.objects.select_related("user").filter(user__supabase_user_id=parsed).first()
        if not profile:
            raise DeliveryDriverAdminError("delivery_not_found", "Repartidor no encontrado.")
        return profile

    def _find_user(self, user_id: str):
        try:
            parsed = UUID(str(user_id))
        except (TypeError, ValueError):
            return None
        return UserProfile.objects.filter(supabase_user_id=parsed).first()

    def _serialize(self, profile: DeliveryProfile):
        active_assignments_count = DeliveryAssignment.objects.filter(
            delivery_user=profile.user,
            status__in=[
                DeliveryAssignment.Status.ASSIGNED,
                DeliveryAssignment.Status.AT_CHEF,
                DeliveryAssignment.Status.PICKED_UP,
                DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT,
            ],
        ).count()
        return {
            "user_id": str(profile.user.supabase_user_id),
            "email": profile.user.email,
            "first_name": profile.user.first_name,
            "last_name": profile.user.last_name,
            "full_name": profile.user.full_name,
            "phone": profile.user.phone,
            "is_active": profile.user.is_active,
            "vehicle_type": profile.vehicle_type,
            "vehicle_brand": profile.vehicle_brand,
            "vehicle_model": profile.vehicle_model,
            "vehicle_plate": profile.vehicle_plate,
            "vehicle_front_image_url": profile.vehicle_front_image_url,
            "vehicle_rear_image_url": profile.vehicle_rear_image_url,
            "approval_status": profile.approval_status,
            "status_notes": profile.status_notes,
            "availability_manual_status": profile.availability_manual_status,
            "availability_effective_status": profile.availability_effective_status,
            "availability_status_changed_at": profile.availability_status_changed_at.isoformat()
            if profile.availability_status_changed_at
            else None,
            "active_assignments_count": active_assignments_count,
            "capacity_limit": 3,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
        }
