from datetime import datetime, timezone
from uuid import UUID

from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import AuditEvent, UserProfile


class ProfileRepository:
    def get_profile(self, user_id: str):
        profile = self._find_user(user_id)
        if not profile:
            return {}
        return {
            "user_id": str(profile.supabase_user_id),
            "email": profile.email,
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "full_name": profile.full_name,
            "role": profile.role,
            "phone": profile.phone,
            "address": profile.address,
            "accept_terms": profile.accept_terms,
            "notify_gmail": profile.notify_gmail,
            "notify_push": profile.notify_push,
            "updated_at": profile.updated_at,
        }

    def save_profile(self, user_id: str, payload: dict):
        profile = self._find_user(user_id)
        if not profile:
            return {}
        allowed = {"phone", "address", "accept_terms", "notify_gmail", "notify_push"}
        changed = []
        for field, value in payload.items():
            if field not in allowed:
                continue
            value = value.strip() if isinstance(value, str) else value
            if getattr(profile, field) != value:
                setattr(profile, field, value)
                changed.append(field)
        if changed:
            profile.save(update_fields=[*changed, "updated_at"])
        return self.get_profile(user_id)

    def save_chef_profile(self, user_id: str, payload: dict):
        user = self._find_user(user_id)
        if not user:
            return None
        location = payload.get("location") or {}
        specialties = payload.get("specialties", [])
        if isinstance(specialties, str):
            specialties = [item.strip() for item in specialties.split(",") if item.strip()]

        defaults = {
            "business_name": payload.get("business_name", ""),
            "public_description": payload.get("public_description", ""),
            "specialties": specialties,
            "location_latitude": location.get("latitude"),
            "location_longitude": location.get("longitude"),
            "location_address": location.get("address", ""),
            "schedule": payload.get("schedule", ""),
            "profile_image_url": payload.get("profile_image_url", ""),
            "status": payload.get("status", "pending_validation"),
        }
        ChefProfile.objects.update_or_create(user=user, defaults=defaults)
        return self.get_chef_profile(user_id)

    def get_chef_profile(self, user_id: str):
        user = self._find_user(user_id)
        if not user:
            return None
        profile = ChefProfile.objects.filter(user=user, deleted_at__isnull=True).first()
        if not profile:
            return None
        return {
            "user_id": str(user.supabase_user_id),
            "chef_id": str(user.supabase_user_id),
            "business_name": profile.business_name,
            "public_description": profile.public_description,
            "specialties": profile.specialties,
            "location": {
                "latitude": profile.location_latitude,
                "longitude": profile.location_longitude,
                "address": profile.location_address,
            },
            "schedule": profile.schedule,
            "profile_image_url": profile.profile_image_url,
            "status": profile.status,
            "updated_at": profile.updated_at,
        }

    def create_recovery_token(self, email: str):
        raise NotImplementedError("La recuperacion de contrasena se gestiona con Supabase Auth.")

    def consume_recovery_token(self, token: str):
        raise NotImplementedError("La recuperacion de contrasena se gestiona con Supabase Auth.")

    def log_event(self, event: str, details: dict):
        AuditEvent.objects.create(
            event=event,
            details=details,
            at=datetime.now(timezone.utc),
        )

    def _find_user(self, user_id: str):
        try:
            parsed = UUID(str(user_id))
        except (TypeError, ValueError):
            return None
        return UserProfile.objects.filter(supabase_user_id=parsed).first()
