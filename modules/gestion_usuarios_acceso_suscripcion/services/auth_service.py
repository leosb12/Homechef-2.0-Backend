from ..repositories.profile_repository import ProfileRepository
from ..repositories.user_repository import UserRepository
from shared.security.jwt_authentication import generate_access_token, generate_refresh_token

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

    def register(self, payload: dict):
        email = payload["email"].lower().strip()
        if self.user_repo.find_by_email(email):
            raise ValueError("El correo ya esta registrado.")

        user = self.user_repo.create_user(
            email=email,
            password=payload["password"],
            first_name=payload["first_name"],
            last_name=payload["last_name"],
            role=payload["role"],
            phone=payload["phone"],
        )
        self.profile_repo.save_profile(user.id, {"phone": payload["phone"], "address": "", "accept_terms": payload["accept_terms"]})

        if payload["role"] == "COCINERO":
            self.profile_repo.save_chef_profile(
                user.id,
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

        self.profile_repo.log_event("user_registered", {"user_id": user.id, "role": payload["role"]})
        return {"message": "Registro realizado correctamente."}

    def login(self, email: str, password: str):
        user = self.user_repo.verify_credentials(email=email, password=password)
        if not user:
            raise PermissionError("Credenciales no validas.")
        if not user.is_active:
            raise RuntimeError("Cuenta inactiva o bloqueada.")

        role = user.role
        if not role or role not in ROLE_REDIRECTS:
            self.profile_repo.log_event("login_role_config_error", {"user_id": user.id, "role": role})
            raise LookupError("Rol sin configuracion de acceso.")

        access = generate_access_token(user.id, user.email, role)
        refresh = generate_refresh_token(user.id, user.email, role)
        self.profile_repo.log_event("login_success", {"user_id": user.id, "role": role})
        return {
            "access": access,
            "refresh": refresh,
            "role": role,
            "redirect_path": ROLE_REDIRECTS[role],
            "user": {"id": user.id, "email": user.email, "first_name": user.first_name, "last_name": user.last_name},
        }

    def logout(self, user_id: int):
        self.profile_repo.log_event("logout", {"user_id": user_id})
        return {"message": "Sesion finalizada correctamente."}
