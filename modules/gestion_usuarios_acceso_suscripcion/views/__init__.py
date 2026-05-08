from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from ..serializers.auth_serializers import (
    RegisterSerializer,
    UpdateProfileSerializer,
)
from ..services.auth_service import AuthService
from ..services.profile_service import ProfileService
from .ai_subscription_views import (
    audit_log,
    available_plans,
    can_use_ai,
    cancel,
    change_plan,
    coingate_callback,
    confirm_payment_return,
    payment_history,
    renew,
    subscribe,
    stripe_webhook,
    subscription_status,
    subscription_summary,
)


@api_view(["GET"])
@permission_classes([AllowAny])
def module_home(request):
    return Response({"module": "gestion_usuarios_acceso_suscripcion", "status": "ok"})


@api_view(["POST"])
@permission_classes([AllowAny])
def register_view(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        result = AuthService().complete_registration(serializer.validated_data)
        return Response(result, status=status.HTTP_201_CREATED)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_409_CONFLICT)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def login_view(request):
    try:
        result = AuthService().session(request.user)
        return Response(result, status=status.HTTP_200_OK)
    except LookupError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([AllowAny])
def session_view(request):
    try:
        # Si el usuario no está autenticado, devolver null
        if not request.user or not getattr(request.user, 'is_authenticated', False):
            return Response({"user": None}, status=status.HTTP_200_OK)
        
        # Si el usuario está autenticado pero no tiene rol, devolver usuario sin rol
        if not getattr(request.user, 'role', None):
            return Response({
                "user": {
                    "id": request.user.id,
                    "email": request.user.email,
                    "first_name": request.user.first_name,
                    "last_name": request.user.last_name,
                },
                "role": None,
                "redirect_path": "/register"
            }, status=status.HTTP_200_OK)
        
        result = AuthService().session(request.user)
        return Response(result, status=status.HTTP_200_OK)
    except LookupError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    result = AuthService().logout(request.user.id)
    return Response(result, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
def recover_password_request(request):
    return Response(
        {"detail": "La recuperacion de contrasena se gestiona con Supabase Auth desde el cliente."},
        status=status.HTTP_410_GONE,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def recover_password_confirm(request):
    return Response(
        {"detail": "La recuperacion de contrasena se gestiona con Supabase Auth desde el cliente."},
        status=status.HTTP_410_GONE,
    )


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def profile_view(request):
    service = ProfileService()
    if request.method == "GET":
        return Response(service.get_profile(request.user), status=status.HTTP_200_OK)

    serializer = UpdateProfileSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    profile = service.update_profile(request.user, serializer.validated_data)
    return Response(profile, status=status.HTTP_200_OK)


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def change_password(request):
    return Response(
        {"detail": "Las contrasenas se administran con Supabase Auth desde el cliente."},
        status=status.HTTP_410_GONE,
    )
