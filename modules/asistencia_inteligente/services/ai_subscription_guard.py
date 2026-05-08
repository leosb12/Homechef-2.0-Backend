from rest_framework import status
from rest_framework.response import Response

from modules.gestion_usuarios_acceso_suscripcion.exceptions import AISubscriptionError
from modules.gestion_usuarios_acceso_suscripcion.services.subscription_service import AISubscriptionService

class AISubscriptionGuard:
    def validate(self, request):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return Response({'detail': 'JWT requerido'}, status=status.HTTP_401_UNAUTHORIZED)
        if getattr(user, 'role', '').upper() != 'COCINERO':
            return Response({'detail': 'Solo rol COCINERO'}, status=status.HTTP_403_FORBIDDEN)
        try:
            service = AISubscriptionService()
            chef_profile = service.get_chef_profile(user)
            result = service.can_use_ai(chef_profile, request=request)
        except AISubscriptionError as exc:
            return Response({'detail': exc.message}, status=exc.status_code)
        if not result['can_use_ai']:
            return Response({'detail': 'Suscripcion IA inactiva'}, status=status.HTTP_402_PAYMENT_REQUIRED)
        return None
