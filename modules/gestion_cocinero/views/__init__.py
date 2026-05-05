from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ..permissions import IsChefRole
from ..services.chef_services import ChefServices


@api_view(["GET"])
def module_home(request):
    return Response({"module": "gestion_cocinero", "status": "ok"})


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_profile_view(request):
    service = ChefServices()
    if request.method == "GET":
        return Response(service.get_profile(request.user.id), status=status.HTTP_200_OK)
    try:
        data = service.save_profile(request.user.id, request.data)
        return Response(data, status=status.HTTP_200_OK)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["PUT"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_profile_location_view(request):
    service = ChefServices()
    try:
        data = service.save_location(request.user.id, request.data)
        return Response(data, status=status.HTTP_200_OK)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_availability_view(request):
    service = ChefServices()
    if request.method == "GET":
        return Response(service.get_availability(request.user.id), status=status.HTTP_200_OK)
    try:
        data = service.save_availability(request.user.id, request.data)
        return Response(data, status=status.HTTP_200_OK)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_dashboard_view(request):
    return Response(ChefServices().dashboard(request.user.id), status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_dishes_collection_view(request):
    service = ChefServices()
    if request.method == "GET":
        return Response({"items": service.list_dishes(request.user.id)}, status=status.HTTP_200_OK)
    try:
        dish = service.save_dish(request.user.id, request.data)
        return Response(dish, status=status.HTTP_201_CREATED)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["PUT", "DELETE"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_dishes_item_view(request, dish_id: str):
    service = ChefServices()
    if request.method == "DELETE":
        service.delete_dish(request.user.id, dish_id)
        return Response({"message": "Plato eliminado."}, status=status.HTTP_200_OK)
    try:
        if request.data.get("status"):
            dish = service.update_dish_status(request.user.id, dish_id, request.data.get("status"))
        else:
            dish = service.save_dish(request.user.id, {**request.data, "_id": dish_id})
        return Response(dish, status=status.HTTP_200_OK)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_menu_view(request):
    service = ChefServices()
    if request.method == "GET":
        return Response(service.get_daily_menu(request.user.id), status=status.HTTP_200_OK)
    try:
        menu = service.save_daily_menu(request.user.id, request.data)
        return Response(menu, status=status.HTTP_200_OK)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)
