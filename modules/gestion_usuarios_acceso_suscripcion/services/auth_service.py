from ..repositories.profile_repository import ProfileRepository
from ..repositories.user_repository import UserRepository
from modules.confianza_administracion_seguridad.services.audit_service import AuditService
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

    def complete_registration(self, payload: dict, request=None):
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
            upload_service = StorageUploadService()
            raw_user = self.user_repo.find_raw_by_email(email)
            kitchen_photos_urls = []
            
            for photo_file in payload.get("kitchen_photos", []):
                upload = upload_service.upload_for_owner(
                    raw_user,
                    photo_file,
                    file_type="kitchen_photo",
                )
                kitchen_photos_urls.append(upload.public_url)

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
                    "kitchen_photos": kitchen_photos_urls,
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
        raw_user = self.user_repo.find_raw_by_email(email)
        AuditService().log_event(
            event_type="USER_REGISTERED",
            event_category="users",
            action="created",
            entity_type="user",
            entity_id=str(raw_user.id if raw_user else supabase_user_id),
            actor=raw_user,
            target_user_id=str(supabase_user_id),
            target_role=role,
            description=f"Nuevo usuario registrado con rol {role}.",
            new_values={"email": email, "role": role, "phone": payload.get("phone", "")},
            metadata={"registration_channel": "web"},
            request=request,
        )
        if role == "COCINERO":
            AuditService().log_event(
                event_type="CHEF_REGISTERED",
                event_category="chefs",
                action="created",
                entity_type="chef_profile",
                entity_id=str(supabase_user_id),
                actor=raw_user,
                target_user_id=str(supabase_user_id),
                target_role=role,
                description="Registro de cocinero enviado a validacion administrativa.",
                metadata={"status": "pending_validation"},
                request=request,
            )
        if role == "REPARTIDOR":
            AuditService().log_event(
                event_type="RIDER_REGISTERED",
                event_category="riders",
                action="created",
                entity_type="rider_profile",
                entity_id=str(supabase_user_id),
                actor=raw_user,
                target_user_id=str(supabase_user_id),
                target_role=role,
                description="Registro de repartidor enviado a validacion administrativa.",
                metadata={"approval_status": "recien_registrado"},
                request=request,
            )
        return {
            "message": "Perfil registrado correctamente.",
            "role": role,
            "redirect_path": ROLE_REDIRECTS.get(role, "/"),
        }

    def session(self, user, fcm_token=None, request=None):
        role = user.role
        if not role or role not in ROLE_REDIRECTS:
            self.profile_repo.log_event("login_role_config_error", {"user_id": user.id, "role": role})
            AuditService().log_event(
                event_type="USER_LOGIN_FAILED",
                event_category="security",
                action="failed",
                entity_type="user",
                entity_id=str(user.id),
                actor=user,
                description="Inicio de sesion fallido por rol sin configuracion.",
                metadata={"role": role},
                request=request,
                severity="warning",
                status="failed",
            )
            raise LookupError("Rol sin configuracion de acceso.")
        
        redirect_path = ROLE_REDIRECTS[role]

        if role == "COCINERO":
            chef_profile = self.profile_repo.get_chef_profile(user.id)
            if chef_profile:
                if chef_profile.get("status") == "pending_validation":
                    redirect_path = "/chef/pending"
                elif chef_profile.get("status") == "rejected":
                    redirect_path = "/chef/rejected"

        if fcm_token:
            profile = self.user_repo.find_raw_by_email(user.email)
            if profile:
                profile.fcm_token = fcm_token
                profile.save(update_fields=['fcm_token'])
            
        self.profile_repo.log_event("login_success", {"user_id": user.id, "role": role})
        AuditService().log_event(
            event_type="ADMIN_LOGIN" if role == "ADMINISTRADOR" else "USER_LOGIN",
            event_category="security",
            action="login",
            entity_type="user",
            entity_id=str(user.id),
            actor=user,
            description="Administrador inicio sesion correctamente." if role == "ADMINISTRADOR" else "Usuario inicio sesion correctamente.",
            metadata={"redirect_path": redirect_path, "fcm_token_updated": bool(fcm_token)},
            request=request,
        )
        user_data = {
            "id": user.id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }
        if role == "COCINERO":
            chef_prof = self.profile_repo.get_chef_profile(user.id)
            if chef_prof:
                user_data["chef_profile"] = chef_prof

        return {
            "role": role,
            "redirect_path": redirect_path,
            "user": user_data,
        }

    def logout(self, user_id: str, request=None, user=None):
        self.profile_repo.log_event("logout", {"user_id": user_id})
        AuditService().log_event(
            event_type="USER_LOGOUT",
            event_category="security",
            action="logout",
            entity_type="user",
            entity_id=str(user_id),
            actor=user,
            actor_user_id=str(user_id),
            description="Sesion finalizada correctamente.",
            request=request,
        )
        return {"message": "Sesion finalizada correctamente."}
