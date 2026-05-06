from dataclasses import dataclass
from uuid import UUID

from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile


@dataclass
class LocalUser:
    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    is_active: bool

    @property
    def is_authenticated(self):
        return True


class UserRepository:
    def find_by_email(self, email: str):
        doc = UserProfile.objects.filter(email=email.lower().strip()).first()
        return self._to_user(doc) if doc else None

    def find_raw_by_email(self, email: str):
        return UserProfile.objects.filter(email=email.lower().strip()).first()

    def find_by_id(self, user_id: str):
        profile = self._find_profile(user_id)
        return self._to_user(profile) if profile else None

    def update_basic_data(self, user_id: str, payload: dict):
        profile = self._find_profile(user_id)
        if not profile:
            return None

        allowed_fields = {"first_name", "last_name", "full_name", "phone", "role", "email", "address", "accept_terms"}
        changed = []
        for field, value in payload.items():
            if field not in allowed_fields:
                continue
            value = value.strip() if isinstance(value, str) else value
            if getattr(profile, field) != value:
                setattr(profile, field, value)
                changed.append(field)
        if any(field in changed for field in ("first_name", "last_name")) and "full_name" not in changed:
            profile.full_name = f"{profile.first_name} {profile.last_name}".strip()
            changed.append("full_name")
        if changed:
            profile.save(update_fields=[*changed, "updated_at"])
        return self._to_user(profile)

    def create_or_update_profile(self, user_id: str, payload: dict):
        profile = self._upsert_profile(user_id, payload)
        return self._to_user(profile) if profile else None

    def create_user(self, *args, **kwargs):
        raise NotImplementedError("Los usuarios finales se crean con Supabase Auth, no con Django.")

    def verify_credentials(self, *args, **kwargs):
        raise NotImplementedError("Las credenciales se validan con Supabase Auth, no con Django.")

    def update_password(self, *args, **kwargs):
        raise NotImplementedError("Las contrasenas se administran con Supabase Auth.")

    def _find_profile(self, user_id: str):
        try:
            parsed = UUID(str(user_id))
        except (TypeError, ValueError):
            return None
        return UserProfile.objects.filter(supabase_user_id=parsed).first()

    def _upsert_profile(self, user_id: str, payload: dict):
        try:
            parsed = UUID(str(user_id))
        except (TypeError, ValueError):
            return None

        defaults = {
            "email": payload.get("email", "").lower().strip(),
            "first_name": payload.get("first_name", "").strip(),
            "last_name": payload.get("last_name", "").strip(),
            "full_name": payload.get("full_name", "").strip(),
            "phone": payload.get("phone", "").strip(),
            "role": payload.get("role", UserProfile.ROLE_CLIENT),
            "accept_terms": payload.get("accept_terms", False),
        }
        profile, _ = UserProfile.objects.update_or_create(supabase_user_id=parsed, defaults=defaults)
        return profile

    def _to_user(self, profile: UserProfile):
        return LocalUser(
            id=str(profile.supabase_user_id),
            email=profile.email,
            first_name=profile.first_name,
            last_name=profile.last_name,
            role=profile.role,
            is_active=profile.is_active,
        )
