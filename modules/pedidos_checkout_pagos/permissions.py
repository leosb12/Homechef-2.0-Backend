from rest_framework.permissions import BasePermission


class IsDeliveryRole(BasePermission):
    message = "Solo repartidores pueden acceder a este recurso."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        role = str(getattr(user, "role", "") or "").strip().upper()
        return bool(user and getattr(user, "is_authenticated", False) and role == "REPARTIDOR")
