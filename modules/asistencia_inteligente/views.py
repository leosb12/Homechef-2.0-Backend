from rest_framework.decorators import api_view
from rest_framework.response import Response
from .services.ai_subscription_guard import AISubscriptionGuard
from .services.cooking_assistant_service import CookingAssistantService
from .services.vision_analysis_service import VisionAnalysisService
from .services.production_pricing_service import ProductionPricingService
from .services.ai_publication_helper_service import AIPublicationHelperService

@api_view(['GET'])
def ai_module_home(request):
    return Response({'module': 'asistencia_inteligente', 'status': 'ok'})

def _guard(request):
    return AISubscriptionGuard().validate(request)

@api_view(['POST'])
def cooking_assistant(request):
    denied = _guard(request)
    return denied or Response(CookingAssistantService().run(request.data))

@api_view(['POST'])
def analyze_image(request):
    denied = _guard(request)
    return denied or Response(VisionAnalysisService().run(request.data))

@api_view(['POST'])
def suggest_production_price(request):
    denied = _guard(request)
    return denied or Response(ProductionPricingService().run(request.data))

@api_view(['POST'])
def publication_helper(request):
    from rest_framework import status
    from modules.gestion_usuarios_acceso_suscripcion.services.ia_access_service import IAAccessService
    
    access_result = IAAccessService().validar_acceso_ia(request.user, 'publicacion_platos')
    if not access_result.get('permitido', False):
        code = access_result.get('codigo', 'PLAN_SIN_IA')
        status_code = status.HTTP_403_FORBIDDEN
        if code == 'USUARIO_NO_AUTENTICADO':
            status_code = status.HTTP_401_UNAUTHORIZED
        elif code in ['SUSCRIPCION_INEXISTENTE', 'SUSCRIPCION_INACTIVA', 'PLAN_SIN_IA']:
            status_code = status.HTTP_402_PAYMENT_REQUIRED
            
        msg = access_result.get('mensaje')
        if code in ['PLAN_SIN_IA', 'SUSCRIPCION_INEXISTENTE', 'SUSCRIPCION_INACTIVA']:
            msg = "Función premium. Activa un plan IA con asistencia en publicaciones para generar título, descripción, etiquetas, categorías y precio sugerido automáticamente."
            
        return Response({'detail': msg, 'code': code}, status=status_code)
        
    return Response(AIPublicationHelperService().run(request.data))
