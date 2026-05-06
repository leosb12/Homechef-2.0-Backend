from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from ..permissions import IsClienteRole
from ..services.dish_detail_service import DishDetailService
from ..services.favorites_preferences_service import FavoritesPreferencesService
from ..services.reputation_service import ReputationService
from ..serializers.public_dashboard_serializer import PublicDashboardResponseSerializer
from ..services.client_explore_service import ClientExploreService
from ..services.public_dashboard_service import PublicDashboardService

@api_view(['GET'])
def module_home(request):
    return Response({'module': 'marketplace_platos', 'status': 'ok'})


@api_view(["GET"])
@permission_classes([AllowAny])
def public_dashboard(request):
    try:
        payload = PublicDashboardService().get_public_dashboard()
        serializer = PublicDashboardResponseSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)
    except Exception:
        return Response(
            {
                "status": "error",
                "message": "Error temporal al cargar el dashboard publico. Intenta nuevamente.",
                "dishes": [],
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsClienteRole])
def client_explore_dashboard(request):
    try:
        query = request.query_params.get("q", "").strip()
        featured = request.query_params.get("featured", "").strip().lower()
        sort = request.query_params.get("sort", "").strip().lower()
        min_price = request.query_params.get("min_price", "").strip()
        max_price = request.query_params.get("max_price", "").strip()
        availability = request.query_params.get("availability", "").strip().lower()
        cuisine_type = request.query_params.get("cuisine_type", "").strip().lower()
        diet_type = request.query_params.get("diet_type", "").strip().lower()
        location_available = request.query_params.get("location_available", "true").strip().lower()
        latitude = request.query_params.get("latitude", "").strip()
        longitude = request.query_params.get("longitude", "").strip()
        if location_available == "false":
            latitude = ""
            longitude = ""
        payload = ClientExploreService().get_explore_dashboard(
            query=query,
            featured=featured,
            sort=sort,
            min_price=min_price,
            max_price=max_price,
            availability=availability,
            cuisine_type=cuisine_type,
            diet_type=diet_type,
            location_available=location_available,
            latitude=latitude,
            longitude=longitude,
        )
        serializer = PublicDashboardResponseSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)
    except Exception:
        return Response(
            {
                "status": "error",
                "message": "Error temporal al cargar la exploracion de platos. Intenta nuevamente.",
                "dishes": [],
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsClienteRole])
def dish_detail(request, dish_id: str):
    try:
        detail = DishDetailService().get_detail(dish_id)
        if not detail:
            return Response({"detail": "Plato no disponible."}, status=status.HTTP_404_NOT_FOUND)
        return Response(detail, status=status.HTTP_200_OK)
    except Exception:
        return Response({"detail": "Error temporal al cargar detalle del plato."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def add_dish_to_cart(request, dish_id: str):
    try:
        quantity = int(request.data.get("quantity", 1))
    except (TypeError, ValueError):
        quantity = 0
    result = DishDetailService().add_to_cart(request.user.id, dish_id, quantity)
    if result.get("ok"):
        return Response(result, status=status.HTTP_200_OK)
    if result.get("code") == "invalid_quantity":
        return Response({"detail": "Cantidad no valida.", "available_portions": result.get("available_portions", 0)}, status=status.HTTP_400_BAD_REQUEST)
    if result.get("code") == "chef_unavailable":
        return Response({"detail": "Cocinero no disponible temporalmente."}, status=status.HTTP_409_CONFLICT)
    return Response({"detail": "Plato no disponible."}, status=status.HTTP_409_CONFLICT)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def favorites_view(request):
    service = FavoritesPreferencesService()
    if request.method == "GET":
        data = service.list_favorites(request.user.id)
        if not data:
            return Response({"status": "empty", "message": "No tienes favoritos registrados.", "items": []}, status=status.HTTP_200_OK)
        return Response({"status": "ok", "items": data}, status=status.HTTP_200_OK)
    try:
        result = service.add_favorite(
            request.user.id,
            request.data.get("favorite_type", ""),
            request.data.get("ref_id", ""),
        )
        if result.get("duplicated"):
            return Response({"message": "El favorito ya estaba guardado."}, status=status.HTTP_200_OK)
        return Response({"message": "Favorito guardado correctamente."}, status=status.HTTP_201_CREATED)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated, IsClienteRole])
def remove_favorite(request, favorite_type: str, ref_id: str):
    try:
        FavoritesPreferencesService().remove_favorite(request.user.id, favorite_type, ref_id)
        return Response({"message": "Favorito removido."}, status=status.HTTP_200_OK)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, IsClienteRole])
def preferences_view(request):
    service = FavoritesPreferencesService()
    if request.method == "GET":
        return Response(service.get_preferences(request.user.id), status=status.HTTP_200_OK)
    try:
        updated = service.save_preferences(request.user.id, request.data)
        return Response(updated, status=status.HTTP_200_OK)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsClienteRole])
def chef_reputation(request, chef_id: str):
    try:
        data = ReputationService().get_reputation(chef_id)
        return Response(data, status=status.HTTP_200_OK)
    except Exception:
        return Response({"detail": "Error temporal al consultar reputación."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsClienteRole])
def chef_public_profile(request, chef_id: str):
    try:
        data = ReputationService().get_public_profile(chef_id)
        if not data:
            return Response({"detail": "Cocinero no encontrado."}, status=status.HTTP_404_NOT_FOUND)
        return Response(data, status=status.HTTP_200_OK)
    except Exception:
        return Response({"detail": "Error temporal al consultar perfil del cocinero."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def create_chef_review(request, chef_id: str):
    try:
        review = ReputationService().create_review(request.user.id, chef_id, request.data)
        return Response(review, status=status.HTTP_201_CREATED)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception:
        return Response({"detail": "Error temporal al registrar reseña."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def create_dish_review(request, dish_id: str):
    try:
        review = ReputationService().create_dish_review(request.user.id, dish_id, request.data)
        return Response(review, status=status.HTTP_201_CREATED)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception:
        return Response({"detail": "Error temporal al registrar reseña."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
