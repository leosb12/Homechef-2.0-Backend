from rest_framework.permissions import BasePermission


class IsActiveChef(BasePermission):
    message = "Solo cocineros activos pueden gestionar suscripcion IA."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        role = str(getattr(user, "role", "") or "").upper()
        return bool(
            user
            and getattr(user, "is_authenticated", False)
            and getattr(user, "is_active", False)
            and role == "COCINERO"
        )
