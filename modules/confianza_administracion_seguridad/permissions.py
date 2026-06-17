from rest_framework.permissions import BasePermission
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile

class IsAdminRole(BasePermission):
    message = "Solo administradores pueden acceder."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(user and getattr(user, "is_authenticated", False) and getattr(user, "role", "") == UserProfile.ROLE_ADMIN)
