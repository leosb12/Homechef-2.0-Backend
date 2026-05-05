from ..repositories.profile_repository import ProfileRepository
from ..repositories.user_repository import UserRepository


class RecoveryService:
    def __init__(self):
        self.user_repo = UserRepository()
        self.profile_repo = ProfileRepository()

    def request_recovery(self, email: str):
        user = self.user_repo.find_by_email(email.lower().strip())
        if not user:
            raise ValueError("No se pudo procesar la recuperacion.")
        token = self.profile_repo.create_recovery_token(user.email)
        self.profile_repo.log_event("password_recovery_requested", {"user_id": user.id})
        return {"message": "Se enviaron instrucciones de recuperacion.", "recovery_token_stub": token}

    def confirm_recovery(self, token: str, new_password: str):
        email = self.profile_repo.consume_recovery_token(token)
        if not email:
            raise PermissionError("El enlace o codigo es invalido o expiro.")
        user = self.user_repo.find_by_email(email)
        if not user:
            raise ValueError("No se pudo procesar la recuperacion.")
        self.user_repo.update_password(user.id, new_password)
        self.profile_repo.log_event("password_recovery_completed", {"user_id": user.id})
        return {"message": "Contrasena actualizada correctamente."}
