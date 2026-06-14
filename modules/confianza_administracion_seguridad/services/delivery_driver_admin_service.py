from uuid import UUID

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

    def update_status(self, actor_user_id: str, delivery_user_id: str, next_status: str):
        actor = self._find_user(actor_user_id)
        if not actor or actor.role != UserProfile.ROLE_ADMIN:
            raise DeliveryDriverAdminError("admin_required", "Solo un administrador puede gestionar repartidores.")

        if next_status not in {
            DeliveryProfile.ApprovalStatus.ACTIVE,
            DeliveryProfile.ApprovalStatus.SUSPENDED,
        }:
            raise DeliveryDriverAdminError("invalid_status", "Estado de repartidor invalido.")

        profile = self._find_delivery_profile(delivery_user_id)
        profile.approval_status = next_status
        profile.status_notes = (
            "Repartidor habilitado por administracion."
            if next_status == DeliveryProfile.ApprovalStatus.ACTIVE
            else "Repartidor suspendido por administracion."
        )
        profile.save(update_fields=["approval_status", "status_notes", "updated_at"])
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
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
        }
