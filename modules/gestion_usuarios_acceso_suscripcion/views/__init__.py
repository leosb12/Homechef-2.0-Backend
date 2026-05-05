from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from ..serializers.auth_serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    RecoverPasswordConfirmSerializer,
    RecoverPasswordRequestSerializer,
    RegisterSerializer,
    UpdateProfileSerializer,
)
from ..services.auth_service import AuthService
from ..services.profile_service import ProfileService
from ..services.recovery_service import RecoveryService


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
        result = AuthService().register(serializer.validated_data)
        return Response(result, status=status.HTTP_201_CREATED)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_409_CONFLICT)


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        result = AuthService().login(
            serializer.validated_data["email"],
            serializer.validated_data["password"],
        )
        return Response(result, status=status.HTTP_200_OK)
    except PermissionError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_401_UNAUTHORIZED)
    except RuntimeError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_403_FORBIDDEN)
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
    serializer = RecoverPasswordRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        result = RecoveryService().request_recovery(serializer.validated_data["email"])
        return Response(result, status=status.HTTP_200_OK)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_404_NOT_FOUND)


@api_view(["POST"])
@permission_classes([AllowAny])
def recover_password_confirm(request):
    serializer = RecoverPasswordConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        result = RecoveryService().confirm_recovery(
            serializer.validated_data["token"],
            serializer.validated_data["password"],
        )
        return Response(result, status=status.HTTP_200_OK)
    except PermissionError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_404_NOT_FOUND)


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
    serializer = ChangePasswordSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        result = ProfileService().change_password(
            request.user,
            serializer.validated_data["current_password"],
            serializer.validated_data["new_password"],
        )
        return Response(result, status=status.HTTP_200_OK)
    except PermissionError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)
