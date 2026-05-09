from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils.dateparse import parse_datetime

from modules.sync.serializers import SyncRequestSerializer
from modules.sync.services import SyncService


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def sync_view(request):
    service = SyncService()
    if request.method == "GET":
        raw_last_sync = request.query_params.get("lastSync") or request.query_params.get("last_sync")
        last_sync = parse_datetime(raw_last_sync) if raw_last_sync else None
        return Response(service.get_changes(request.user, last_sync), status=status.HTTP_200_OK)

    serializer = SyncRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        return Response(service.sync_operations(request.user, serializer.validated_data), status=status.HTTP_200_OK)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
