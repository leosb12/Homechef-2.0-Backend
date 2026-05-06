from django.urls import include, path
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

@api_view(['GET'])
@permission_classes([AllowAny])
def health(request):
    return Response({'status': 'ok', 'service': 'homechef-backend'})

urlpatterns = [
    path('api/v1/health/', health),
    path('api/v1/auth/', include('modules.gestion_usuarios_acceso_suscripcion.urls')),
    path('api/v1/marketplace/', include('modules.marketplace_platos.urls')),
    path('api/v1/chef/', include('modules.gestion_cocinero.urls')),
    path('api/v1/uploads/', include('modules.storage_uploads.urls')),
    path('api/v1/ai/', include('modules.asistencia_inteligente.urls')),
    path('api/v1/orders/', include('modules.pedidos_checkout_pagos.urls')),
    path('api/v1/logistics/', include('modules.delivery_logistica.urls')),
    path('api/v1/trust-admin/', include('modules.confianza_administracion_seguridad.urls')),
]
