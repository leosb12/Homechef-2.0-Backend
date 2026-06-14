from ..repositories.profile_repository import ProfileRepository
from ..repositories.user_repository import UserRepository
from modules.storage_uploads.services import StorageUploadService

ROLE_REDIRECTS = {
    "CLIENTE": "/client/explore",
    "COCINERO": "/chef/dashboard",
    "ADMINISTRADOR": "/admin/dashboard",
    "REPARTIDOR": "/delivery/assigned",
}


class AuthService:
    def __init__(self):
        self.user_repo = UserRepository()
        self.profile_repo = ProfileRepository()

    def complete_registration(self, payload: dict):
        role = payload["role"]
        email = payload["email"].lower().strip()
        supabase_user_id = payload["supabase_user_id"]
        full_name = f"{payload.get('first_name', '').strip()} {payload.get('last_name', '').strip()}".strip()

        existing = self.user_repo.find_raw_by_email(email)
        if existing and str(existing.supabase_user_id) != str(supabase_user_id):
            raise ValueError("El correo electronico ya esta registrado.")

        self.user_repo.create_or_update_profile(
            supabase_user_id,
            {
                "email": email,
                "first_name": payload["first_name"],
                "last_name": payload["last_name"],
                "full_name": full_name,
                "phone": payload.get("phone", ""),
                "role": role,
                "accept_terms": payload["accept_terms"],
            },
        )
        self.profile_repo.save_profile(
            supabase_user_id,
            {
                "phone": payload.get("phone", ""),
                "address": "",
                "accept_terms": payload["accept_terms"],
            },
        )

        if role == "COCINERO":
            self.profile_repo.save_chef_profile(
                supabase_user_id,
                {
                    "specialties": payload["chef_specialties"],
                    "location": {
                        "latitude": payload["chef_latitude"],
                        "longitude": payload["chef_longitude"],
                    },
                    "schedule": payload["chef_schedule"],
                    "status": "pending_validation",
                },
            )
        if role == "REPARTIDOR":
            upload_service = StorageUploadService()
            raw_user = self.user_repo.find_raw_by_email(email)
            front_file = payload["delivery_vehicle_front_photo"]
            rear_file = payload["delivery_vehicle_rear_photo"]
            front_upload = upload_service.upload_for_owner(
                raw_user,
                front_file,
                file_type="delivery_vehicle_front",
            )
            rear_upload = upload_service.upload_for_owner(
                raw_user,
                rear_file,
                file_type="delivery_vehicle_rear",
            )
            self.profile_repo.save_delivery_profile(
                supabase_user_id,
                {
                    "vehicle_type": payload["delivery_vehicle_type"],
                    "vehicle_brand": payload["delivery_vehicle_brand"],
                    "vehicle_model": payload["delivery_vehicle_model"],
                    "vehicle_plate": payload["delivery_vehicle_plate"],
                    "vehicle_front_image_url": front_upload.public_url,
                    "vehicle_rear_image_url": rear_upload.public_url,
                    "approval_status": "recien_registrado",
                    "status_notes": "Solicitud enviada para validacion administrativa.",
                },
            )

        self.profile_repo.log_event("user_profile_completed", {"user_id": str(supabase_user_id), "role": role})
        return {
            "message": "Perfil registrado correctamente.",
            "role": role,
            "redirect_path": ROLE_REDIRECTS.get(role, "/"),
        }

    def session(self, user):
        role = user.role
        if not role or role not in ROLE_REDIRECTS:
            self.profile_repo.log_event("login_role_config_error", {"user_id": user.id, "role": role})
            raise LookupError("Rol sin configuracion de acceso.")
        self.profile_repo.log_event("login_success", {"user_id": user.id, "role": role})
        return {
            "role": role,
            "redirect_path": ROLE_REDIRECTS[role],
            "user": {
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
            },
        }

    def logout(self, user_id: str):
        self.profile_repo.log_event("logout", {"user_id": user_id})
        return {"message": "Sesion finalizada correctamente."}
