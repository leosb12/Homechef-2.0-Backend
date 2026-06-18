from datetime import datetime, timezone
from uuid import UUID

from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import AuditEvent, DeliveryProfile, UserProfile


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
            "avatar_url": profile.avatar_url,
            "role": profile.role,
            "phone": profile.phone,
            "address": profile.address,
            "location": {
                "latitude": profile.location_latitude,
                "longitude": profile.location_longitude,
                "address": profile.address,
            },
            "accept_terms": profile.accept_terms,
            "notify_gmail": profile.notify_gmail,
            "notify_push": profile.notify_push,
            "updated_at": profile.updated_at,
        }

    def save_profile(self, user_id: str, payload: dict):
        profile = self._find_user(user_id)
        if not profile:
            return {}
        allowed = {
            "phone",
            "address",
            "avatar_url",
            "location_latitude",
            "location_longitude",
            "accept_terms",
            "notify_gmail",
            "notify_push",
        }
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
            "kitchen_photos": payload.get("kitchen_photos", []),
        }
        ChefProfile.objects.update_or_create(user=user, defaults=defaults)
        return self.get_chef_profile(user_id)

    def save_delivery_profile(self, user_id: str, payload: dict):
        user = self._find_user(user_id)
        if not user:
            return None
        defaults = {
            "vehicle_type": payload.get("vehicle_type", DeliveryProfile.VehicleType.MOTORCYCLE),
            "vehicle_brand": payload.get("vehicle_brand", "").strip(),
            "vehicle_model": payload.get("vehicle_model", "").strip(),
            "vehicle_plate": payload.get("vehicle_plate", "").strip().upper(),
            "vehicle_front_image_url": payload.get("vehicle_front_image_url", "").strip(),
            "vehicle_rear_image_url": payload.get("vehicle_rear_image_url", "").strip(),
            "approval_status": payload.get(
                "approval_status",
                DeliveryProfile.ApprovalStatus.RECENTLY_REGISTERED,
            ),
            "status_notes": payload.get("status_notes", "").strip(),
        }
        DeliveryProfile.objects.update_or_create(user=user, defaults=defaults)
        return self.get_delivery_profile(user_id)

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
            "kitchen_photos": profile.kitchen_photos,
            "updated_at": profile.updated_at,
        }

    def get_delivery_profile(self, user_id: str):
        user = self._find_user(user_id)
        if not user:
            return None
        profile = DeliveryProfile.objects.filter(user=user).first()
        if not profile:
            return None
        return {
            "user_id": str(user.supabase_user_id),
            "delivery_id": str(user.supabase_user_id),
            "vehicle_type": profile.vehicle_type,
            "vehicle_brand": profile.vehicle_brand,
            "vehicle_model": profile.vehicle_model,
            "vehicle_plate": profile.vehicle_plate,
            "vehicle_front_image_url": profile.vehicle_front_image_url,
            "vehicle_rear_image_url": profile.vehicle_rear_image_url,
            "approval_status": profile.approval_status,
            "status_notes": profile.status_notes,
            "updated_at": profile.updated_at,
        }

    def create_recovery_token(self, email: str):
        raise NotImplementedError("La recuperacion de Contraseña se gestiona con Supabase Auth.")

    def consume_recovery_token(self, token: str):
        raise NotImplementedError("La recuperacion de Contraseña se gestiona con Supabase Auth.")

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
