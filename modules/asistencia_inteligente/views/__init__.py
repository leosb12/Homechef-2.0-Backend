from rest_framework.decorators import api_view
from rest_framework.response import Response
from ..services.ai_subscription_guard import AISubscriptionGuard
from ..services.cooking_assistant_service import CookingAssistantService
from ..services.vision_analysis_service import VisionAnalysisService
from ..services.production_pricing_service import ProductionPricingService
from ..services.ai_publication_helper_service import AIPublicationHelperService

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
    denied = _guard(request)
    return denied or Response(AIPublicationHelperService().run(request.data))
