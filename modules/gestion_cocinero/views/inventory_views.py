from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ..permissions import IsChefRole
from ..services.inventory_service import InventoryService


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_inventory_collection_view(request):
    service = InventoryService()
    if request.method == "GET":
        return Response({"items": service.list_inventory(request.user.id)}, status=status.HTTP_200_OK)
    try:
        item = service.save_inventory_item(request.user.id, request.data)
        return Response(item, status=status.HTTP_201_CREATED)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["PUT", "DELETE"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_inventory_item_view(request, item_id: int):
    service = InventoryService()
    if request.method == "DELETE":
        try:
            service.delete_inventory_item(request.user.id, item_id)
            return Response({"message": "Insumo eliminado."}, status=status.HTTP_200_OK)
        except ValueError as ex:
            return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)
    try:
        item = service.save_inventory_item(request.user.id, {**request.data, "id": item_id})
        return Response(item, status=status.HTTP_200_OK)
    except ValueError as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)
