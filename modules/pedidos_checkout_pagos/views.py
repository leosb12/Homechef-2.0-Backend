from rest_framework.decorators import api_view
from rest_framework.response import Response

@api_view(['GET'])
def module_home(request):
    return Response({'module': 'pedidos_checkout_pagos', 'status': 'ok'})
