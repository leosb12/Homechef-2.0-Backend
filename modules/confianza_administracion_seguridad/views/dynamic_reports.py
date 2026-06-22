import requests
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.http import StreamingHttpResponse
from modules.confianza_administracion_seguridad.services.dynamic_reports_service import DynamicReportsBFFService
from modules.confianza_administracion_seguridad.permissions import IsAdminRole
from modules.confianza_administracion_seguridad.services.audit_service import AuditService

class DynamicReportsChatView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, *args, **kwargs):
        prompt = request.data.get("prompt")
        if not prompt:
            return Response({"error": "Prompt es requerido"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            result = DynamicReportsBFFService.process_chat(prompt)
            AuditService().log_event(
                event_type="REPORT_VIEWED",
                event_category="admin",
                action="viewed",
                entity_type="admin_action",
                entity_id="dynamic_reports_chat",
                actor=request.user,
                description="Administrador genero consulta de reporte dinamico.",
                metadata={"prompt": prompt, "row_count": len(result.get("raw_data", [])) if isinstance(result, dict) else None},
                request=request,
            )
            return Response(result, status=status.HTTP_200_OK)
        except Exception as e:
            AuditService().log_event(
                event_type="REPORT_GENERATION_FAILED",
                event_category="system",
                action="failed",
                entity_type="admin_action",
                entity_id="dynamic_reports_chat",
                actor=request.user,
                description="Fallo la generacion de reporte dinamico.",
                metadata={"prompt": prompt, "error": str(e)},
                request=request,
                severity="warning",
                status="failed",
            )
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class DynamicReportsExportView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, *args, **kwargs):
        format = request.data.get("format")
        raw_data = request.data.get("raw_data")
        prompt = request.data.get("prompt")
        charts = request.data.get("charts")
        kpis = request.data.get("kpis")
        title = request.data.get("title")
        
        if not all([format, raw_data, prompt]):
            return Response({"error": "Faltan parámetros"}, status=status.HTTP_400_BAD_REQUEST)
            
        export_url = DynamicReportsBFFService.get_ai_service_url("/export")
        try:
            payload = {
                "format": format,
                "raw_data": raw_data,
                "prompt": prompt,
            }
            if charts:
                payload["charts"] = charts
            if kpis:
                payload["kpis"] = kpis
            if title:
                payload["title"] = title
                
            response = requests.post(export_url, json=payload, stream=True)
            
            response.raise_for_status()
            
            # Reenviamos el stream de FastAPI al Frontend
            http_response = StreamingHttpResponse(
                response.iter_content(chunk_size=8192),
                content_type=response.headers.get("Content-Type")
            )
            http_response['Content-Disposition'] = response.headers.get('Content-Disposition')
            AuditService().log_event(
                event_type="REPORT_EXPORTED",
                event_category="admin",
                action="exported",
                entity_type="admin_action",
                entity_id="dynamic_reports_export",
                actor=request.user,
                description=f"Administrador exporto reporte dinamico en formato {format}.",
                metadata={"format": format, "prompt": prompt, "title": title, "rows": len(raw_data) if isinstance(raw_data, list) else None},
                request=request,
            )
            return http_response
            
        except Exception as e:
            AuditService().log_event(
                event_type="REPORT_EXPORT_FAILED",
                event_category="system",
                action="failed",
                entity_type="admin_action",
                entity_id="dynamic_reports_export",
                actor=request.user,
                description="Fallo la exportacion de reporte dinamico.",
                metadata={"format": format, "prompt": prompt, "error": str(e)},
                request=request,
                severity="warning",
                status="failed",
            )
            return Response({"error": f"Error exportando: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
