from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
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
from .ia_access_views import usar_funcion_ia


@api_view(["GET"])
@permission_classes([AllowAny])
def module_home(request):
    return Response({"module": "gestion_usuarios_acceso_suscripcion", "status": "ok"})


@api_view(["POST"])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def register_view(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        validated_data = serializer.validated_data
        if validated_data.get("role") == "COCINERO":
            kitchen_photos = request.FILES.getlist('kitchen_photos')
            if not kitchen_photos or len(kitchen_photos) < 1 or len(kitchen_photos) > 3:
                return Response({"detail": "Debes adjuntar entre 1 y 3 fotos de tu cocina."}, status=status.HTTP_400_BAD_REQUEST)
            for photo in kitchen_photos:
                if not photo.name.lower().endswith(('.png', '.jpg', '.jpeg')):
                    return Response({"detail": "Solo se permiten fotos en formato PNG o JPG."}, status=status.HTTP_400_BAD_REQUEST)
            validated_data["kitchen_photos"] = kitchen_photos

        result = AuthService().complete_registration(validated_data, request=request)
        return Response(result, status=status.HTTP_201_CREATED)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_409_CONFLICT)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def login_view(request):
    try:
        fcm_token = request.data.get('fcm_token')
        result = AuthService().session(request.user, fcm_token=fcm_token, request=request)
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
        fcm_token = request.data.get('fcm_token') if request.method == 'POST' else None
        result = AuthService().session(request.user, fcm_token=fcm_token, request=request)
        return Response(result, status=status.HTTP_200_OK)
    except LookupError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    result = AuthService().logout(request.user.id, request=request, user=request.user)
    return Response(result, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
def recover_password_request(request):
    return Response(
        {"detail": "La recuperacion de Contraseña se gestiona con Supabase Auth desde el cliente."},
        status=status.HTTP_410_GONE,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def recover_password_confirm(request):
    return Response(
        {"detail": "La recuperacion de Contraseña se gestiona con Supabase Auth desde el cliente."},
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
    profile = service.update_profile(request.user, serializer.validated_data, request=request)
    return Response(profile, status=status.HTTP_200_OK)


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def change_password(request):
    return Response(
        {"detail": "Las Contraseñas se administran con Supabase Auth desde el cliente."},
        status=status.HTTP_410_GONE,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def chef_resubmit_view(request):
    user = request.user
    if user.role != "COCINERO":
        return Response({"detail": "Solo los cocineros pueden usar esta acción."}, status=status.HTTP_403_FORBIDDEN)
    
    kitchen_photos = request.FILES.getlist('kitchen_photos')
    if not kitchen_photos or len(kitchen_photos) < 1 or len(kitchen_photos) > 3:
        return Response({"detail": "Debes adjuntar entre 1 y 3 fotos de tu cocina."}, status=status.HTTP_400_BAD_REQUEST)
    for photo in kitchen_photos:
        if not photo.name.lower().endswith(('.png', '.jpg', '.jpeg')):
            return Response({"detail": "Solo se permiten fotos en formato PNG o JPG."}, status=status.HTTP_400_BAD_REQUEST)

    from modules.storage_uploads.services import StorageUploadService
    from modules.gestion_usuarios_acceso_suscripcion.repositories.user_repository import UserRepository
    from modules.gestion_usuarios_acceso_suscripcion.repositories.profile_repository import ProfileRepository

    upload_service = StorageUploadService()
    user_repo = UserRepository()
    raw_user = user_repo.find_raw_by_email(user.email)
    
    kitchen_photos_urls = []
    for photo_file in kitchen_photos:
        upload = upload_service.upload_for_owner(
            raw_user,
            photo_file,
            file_type="kitchen_photo",
        )
        kitchen_photos_urls.append(upload.public_url)

    profile_repo = ProfileRepository()
    current_chef = profile_repo.get_chef_profile(user.id)
    if not current_chef:
        return Response({"detail": "Perfil de cocinero no encontrado."}, status=status.HTTP_404_NOT_FOUND)

    profile_repo.save_chef_profile(
        user.supabase_user_id,
        {
            "business_name": request.data.get("business_name", current_chef.get("business_name", "")),
            "public_description": request.data.get("public_description", current_chef.get("public_description", "")),
            "specialties": request.data.get("chef_specialties", current_chef.get("specialties", [])),
            "location": {
                "latitude": request.data.get("chef_latitude", current_chef.get("location", {}).get("latitude")),
                "longitude": request.data.get("chef_longitude", current_chef.get("location", {}).get("longitude")),
                "address": request.data.get("address", current_chef.get("location", {}).get("address", "")),
            },
            "schedule": request.data.get("chef_schedule", current_chef.get("schedule", "")),
            "status": "pending_validation",
            "kitchen_photos": kitchen_photos_urls,
        }
    )

    return Response({"message": "Solicitud re-enviada con éxito."}, status=status.HTTP_200_OK)
