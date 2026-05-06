from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from modules.storage_uploads.serializers import UploadedFileSerializer
from modules.storage_uploads.services import StorageUploadService, SupabaseStorageError


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_file_view(request):
    file_obj = request.FILES.get("file")
    if not file_obj:
        return Response({"detail": "Archivo requerido."}, status=status.HTTP_400_BAD_REQUEST)

    file_type = request.data.get("type", "general")
    try:
        uploaded = StorageUploadService().upload_for_user(request.user, file_obj, file_type=file_type)
    except SupabaseStorageError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response(UploadedFileSerializer(uploaded).data, status=status.HTTP_201_CREATED)
