from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ..permissions import IsChefRole
from ..services.finance_service import FinanceService


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_finances_summary_view(request):
    """
    Retorna el resumen financiero del cocinero.
    Permite filtros opcionales en la URL: ?start_date=2026-05-01&end_date=2026-05-31
    """
    start_date = request.query_params.get('start_date')
    end_date = request.query_params.get('end_date')
    
    service = FinanceService()
    try:
        data = service.get_finances_summary(request.user.id, start_date, end_date)
        return Response(data, status=status.HTTP_200_OK)
    except Exception as ex:
        return Response({"detail": str(ex)}, status=status.HTTP_400_BAD_REQUEST)
