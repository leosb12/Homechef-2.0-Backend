from rest_framework import status
from rest_framework.response import Response

class AISubscriptionGuard:
    def validate(self, request):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return Response({'detail': 'JWT requerido'}, status=status.HTTP_401_UNAUTHORIZED)
        if request.headers.get('X-User-Role', '').upper() != 'COCINERO':
            return Response({'detail': 'Solo rol COCINERO'}, status=status.HTTP_403_FORBIDDEN)
        if request.headers.get('X-AI-Subscription-Active', 'false').lower() != 'true':
            return Response({'detail': 'Suscripcion IA inactiva'}, status=status.HTTP_402_PAYMENT_REQUIRED)
        if int(request.headers.get('X-AI-Plan-Remaining', '0')) <= 0:
            return Response({'detail': 'Limite IA agotado'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        return None
