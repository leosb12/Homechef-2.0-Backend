from rest_framework.permissions import BasePermission


class IsChefRole(BasePermission):
    message = "Solo cocineros pueden acceder."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(user and getattr(user, "is_authenticated", False) and getattr(user, "role", "") == "COCINERO")
