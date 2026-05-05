from rest_framework.decorators import api_view
from rest_framework.response import Response

@api_view(['GET'])
def module_home(request):
    return Response({'module': 'gestion_usuarios_acceso_suscripcion', 'status': 'ok'})
