from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ..serializers.ia_access_serializers import IAAccessResponseSerializer, IAFunctionUseRequestSerializer
from ..services.ia_access_service import IAAccessService


@api_view(["POST"])
@permission_classes([AllowAny])
def usar_funcion_ia(request):
    serializer = IAFunctionUseRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    result = IAAccessService().validar_acceso_ia(request.user, serializer.validated_data["funcion"])
    response_data = IAAccessResponseSerializer(result).data
    http_status = status.HTTP_401_UNAUTHORIZED if result["codigo"] == "USUARIO_NO_AUTENTICADO" else status.HTTP_200_OK
    return Response(response_data, status=http_status)
