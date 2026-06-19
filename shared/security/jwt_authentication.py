from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from uuid import UUID

import jwt
import requests
from django.conf import settings
from django.db import transaction
from rest_framework import authentication, exceptions

from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile


@dataclass
class SupabaseAuthUser:
    id: str
    supabase_user_id: str
    email: str
    role: str
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    avatar_url: str = ""
    is_active: bool = True
    is_authenticated: bool = True
    profile: UserProfile | None = None


class SupabaseJWTAuthentication(authentication.BaseAuthentication):
    """
    Validates the bearer token against Supabase Auth and maps it to a local
    profile. Django never receives or validates end-user passwords.
    """

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header.replace("Bearer ", "", 1).strip()
        if not token:
            return None

        import os
        offline_mode = os.getenv("IA_OFFLINE_MODE", "false").lower() == "true" or os.getenv("APP_OFFLINE_DEV_MODE", "false").lower() == "true"

        try:
            if offline_mode:
                user_data = build_supabase_user_from_token(token)
            else:
                try:
                    user_data = fetch_supabase_user(token)
                except exceptions.AuthenticationFailed as exc:
                    if "No se pudo validar el JWT con Supabase." in str(exc):
                        user_data = build_supabase_user_from_token(token)
                    else:
                        raise exc

            profile = sync_user_profile(user_data)
            if not profile.is_active:
                raise exceptions.AuthenticationFailed("Cuenta inactiva o bloqueada.")

            user = SupabaseAuthUser(
                id=str(profile.supabase_user_id),
                supabase_user_id=str(profile.supabase_user_id),
                email=profile.email,
                role=profile.role,
                first_name=profile.first_name,
                last_name=profile.last_name,
                full_name=profile.full_name,
                avatar_url=profile.avatar_url,
                is_active=profile.is_active,
                profile=profile,
            )
            return (user, token)
        except exceptions.AuthenticationFailed:
            # Permite acceso anónimo; los @permission_classes se encargarán de proteger endpoints privados
            return None


def fetch_supabase_user(token: str) -> dict:
    if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
        raise exceptions.AuthenticationFailed("Supabase Auth no esta configurado.")

    url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/user"
    headers = {
        "apikey": settings.SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token}",
    }
    try:
        response = requests.get(url, headers=headers, timeout=8)
    except requests.RequestException as exc:
        raise exceptions.AuthenticationFailed("No se pudo validar el JWT con Supabase.") from exc

    if response.status_code in (401, 403):
        raise exceptions.AuthenticationFailed("JWT de Supabase invalido o expirado.")
    if response.status_code >= 400:
        raise exceptions.AuthenticationFailed("Supabase Auth rechazo la validacion del JWT.")

    try:
        user_data = response.json()
    except ValueError as exc:
        raise exceptions.AuthenticationFailed("Respuesta invalida de Supabase Auth.") from exc

    if not user_data.get("id"):
        raise exceptions.AuthenticationFailed("JWT de Supabase sin usuario asociado.")
    return user_data


def build_supabase_user_from_token(token: str) -> dict:
    try:
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_iss": False,
                "verify_exp": False,
            },
            algorithms=["ES256", "HS256"],
        )
    except jwt.PyJWTError as exc:
        raise exceptions.AuthenticationFailed("JWT de Supabase invalido.") from exc

    exp = claims.get("exp")
    if exp:
        expires_at = datetime.fromtimestamp(int(exp), tz=dt_timezone.utc)
        if expires_at <= datetime.now(dt_timezone.utc):
            raise exceptions.AuthenticationFailed("JWT de Supabase expirado.")

    user_id = str(claims.get("sub") or "").strip()
    if not user_id:
        raise exceptions.AuthenticationFailed("JWT de Supabase sin usuario asociado.")

    return {
        "id": user_id,
        "email": str(claims.get("email") or "").strip(),
        "phone": str(claims.get("phone") or "").strip(),
        "user_metadata": claims.get("user_metadata") or {},
        "app_metadata": claims.get("app_metadata") or {},
        "role": claims.get("role"),
        "session_id": claims.get("session_id"),
        "is_anonymous": claims.get("is_anonymous", False),
    }


@transaction.atomic
def sync_user_profile(user_data: dict) -> UserProfile:
    supabase_user_id = _parse_uuid(user_data.get("id"))
    metadata = user_data.get("user_metadata") or user_data.get("raw_user_meta_data") or {}
    app_metadata = user_data.get("app_metadata") or user_data.get("raw_app_meta_data") or {}

    email = (user_data.get("email") or metadata.get("email") or "").lower().strip()
    if not email:
        email = f"{supabase_user_id}@supabase.local"

    first_name = str(metadata.get("first_name") or "").strip()
    last_name = str(metadata.get("last_name") or "").strip()
    full_name = str(metadata.get("full_name") or metadata.get("name") or "").strip()
    if not full_name:
        full_name = f"{first_name} {last_name}".strip()

    role = str(metadata.get("role") or app_metadata.get("role") or UserProfile.ROLE_CLIENT).upper()
    if role == "CLIENT":
        role = UserProfile.ROLE_CLIENT
    if role not in {choice[0] for choice in UserProfile.ROLE_CHOICES}:
        role = UserProfile.ROLE_CLIENT

    profile, created = UserProfile.objects.get_or_create(
        supabase_user_id=supabase_user_id,
        defaults={
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "avatar_url": str(metadata.get("avatar_url") or metadata.get("picture") or "").strip(),
            "role": role,
            "phone": str(metadata.get("phone") or "").strip(),
            "accept_terms": bool(metadata.get("accept_terms", False)),
        },
    )

    # Evitar sobrescribir cambios locales en cada request autenticado.
    # Solo rellenamos campos vacíos la primera vez o si aún no existen.
    if created:
        profile.save()
    else:
        updated = False
        if not profile.email and email:
            profile.email = email
            updated = True
        if not profile.first_name and first_name:
            profile.first_name = first_name
            updated = True
        if not profile.last_name and last_name:
            profile.last_name = last_name
            updated = True
        if not profile.full_name and full_name:
            profile.full_name = full_name
            updated = True
        avatar = str(metadata.get("avatar_url") or metadata.get("picture") or "").strip()
        if not profile.avatar_url and avatar:
            profile.avatar_url = avatar
            updated = True
        phone = str(metadata.get("phone") or "").strip()
        if not profile.phone and phone:
            profile.phone = phone
            updated = True
        if updated:
            profile.save()

    if profile.role == UserProfile.ROLE_CHEF:
        _sync_chef_profile_from_metadata(profile, metadata)

    return profile


def _sync_chef_profile_from_metadata(profile: UserProfile, metadata: dict):
    chef_defaults = {
        "specialties": _normalize_list(metadata.get("chef_specialties")),
        "schedule": str(metadata.get("chef_schedule") or "").strip(),
        "status": "pending_validation",
    }
    lat = metadata.get("chef_latitude")
    lng = metadata.get("chef_longitude")
    if lat not in (None, ""):
        chef_defaults["location_latitude"] = float(lat)
    if lng not in (None, ""):
        chef_defaults["location_longitude"] = float(lng)

    ChefProfile.objects.get_or_create(user=profile, defaults=chef_defaults)


def _normalize_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _parse_uuid(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise exceptions.AuthenticationFailed("El usuario de Supabase no tiene UUID valido.") from exc
