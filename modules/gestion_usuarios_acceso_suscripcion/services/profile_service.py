from ..repositories.profile_repository import ProfileRepository
from ..repositories.user_repository import UserRepository
from modules.confianza_administracion_seguridad.services.audit_service import AuditService


class ProfileService:
    def __init__(self):
        self.profile_repo = ProfileRepository()
        self.user_repo = UserRepository()

    def get_profile(self, user):
        role = user.role
        profile = self.profile_repo.get_profile(user.id)
        chef_profile = self.profile_repo.get_chef_profile(user.id) if role == "COCINERO" else None
        delivery_profile = self.profile_repo.get_delivery_profile(user.id) if role == "REPARTIDOR" else None
        return {
            "id": user.id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "role": role,
            "avatar_url": profile.get("avatar_url", ""),
            "phone": profile.get("phone", ""),
            "address": profile.get("address", ""),
            "location": profile.get("location", {}),
            "notify_gmail": profile.get("notify_gmail", True),
            "notify_push": profile.get("notify_push", True),
            "chef_profile": chef_profile,
            "delivery_profile": delivery_profile,
        }

    def update_profile(self, user, payload: dict, request=None):
        before = self.get_profile(user)
        basic_updates = {}
        if "first_name" in payload:
            basic_updates["first_name"] = payload["first_name"]
        if "last_name" in payload:
            basic_updates["last_name"] = payload["last_name"]
        if basic_updates:
            self.user_repo.update_basic_data(user.id, basic_updates)
        profile_updates = {
            k: v
            for k, v in payload.items()
            if k
            in (
                "phone",
                "address",
                "avatar_url",
                "location_latitude",
                "location_longitude",
                "notify_gmail",
                "notify_push",
            )
        }
        self.profile_repo.save_profile(user.id, profile_updates)
        self.profile_repo.log_event("profile_updated", {"user_id": user.id, "fields": list(payload.keys())})
        fresh_user = self.user_repo.find_by_id(user.id)
        after = self.get_profile(fresh_user)
        AuditService().log_event(
            event_type="USER_PROFILE_UPDATED",
            event_category="users",
            action="updated",
            entity_type="user",
            entity_id=str(user.id),
            actor=fresh_user or user,
            target_user_id=str(getattr(user, "supabase_user_id", user.id)),
            target_role=getattr(user, "role", ""),
            description="Usuario actualizo datos personales.",
            old_values={key: before.get(key) for key in payload.keys() if key in before},
            new_values={key: after.get(key) for key in payload.keys() if key in after},
            metadata={"fields": list(payload.keys())},
            request=request,
        )
        return self.get_profile(fresh_user)

    def change_password(self, user, current_password: str, new_password: str):
        self.profile_repo.log_event("password_change_requested_in_django", {"user_id": user.id})
        raise NotImplementedError("Las Contraseñas se administran con Supabase Auth desde el cliente.")
