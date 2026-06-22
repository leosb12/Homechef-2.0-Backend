from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.confianza_administracion_seguridad.permissions import IsAdminRole
from modules.confianza_administracion_seguridad.services.ai_audit_service import AIAuditService
from modules.confianza_administracion_seguridad.services.audit_service import AuditService


class AdminAuditGeneralView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        return Response(AuditService().list_events(request.query_params), status=status.HTTP_200_OK)


class AdminAuditGeneralSummaryView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        return Response(AuditService().summary(request.query_params), status=status.HTTP_200_OK)


class AdminAuditGeneralDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, audit_id: int):
        payload = AuditService().get_event(audit_id)
        if not payload:
            return Response({"detail": "Evento de auditoria no encontrado."}, status=status.HTTP_404_NOT_FOUND)
        return Response(payload, status=status.HTTP_200_OK)


class AdminAuditGeneralExportView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        return AuditService().export_events(request.query_params)


class AdminAuditAIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        return Response(AIAuditService().list_events(request.query_params), status=status.HTTP_200_OK)


class AdminAuditAICollectionsView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        return Response(AIAuditService().list_collections(), status=status.HTTP_200_OK)


class AdminAuditAISummaryView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        return Response(AIAuditService().summary(request.query_params), status=status.HTTP_200_OK)


class AdminAuditAIDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, event_id: str):
        payload = AIAuditService().get_event(event_id)
        if not payload:
            return Response({"detail": "Evento de auditoria IA no encontrado."}, status=status.HTTP_404_NOT_FOUND)
        return Response(payload, status=status.HTTP_200_OK)


class AdminAuditAIExportView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        return AIAuditService().export_events(request.query_params)
