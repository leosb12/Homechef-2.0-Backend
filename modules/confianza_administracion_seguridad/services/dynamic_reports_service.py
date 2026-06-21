import requests
from django.conf import settings
from .schema_builder import SchemaBuilder
from .sql_executor import SQLExecutor

class DynamicReportsBFFService:
    @staticmethod
    def get_ai_service_url(path: str) -> str:
        base_url = settings.AI_SERVICE_URL.rstrip('/')
        return f"{base_url}/api/v1/ai/reports{path}"

    @staticmethod
    def process_chat(prompt: str) -> dict:
        schema = SchemaBuilder.get_database_schema()
        
        # 1. Solicitar SQL a FastAPI
        sql_url = DynamicReportsBFFService.get_ai_service_url("/generate-sql")
        response = requests.post(sql_url, json={"prompt": prompt, "database_schema": schema})
        response.raise_for_status()
        data = response.json()
        
        if data.get("action") == "clarify":
            return {
                "action": "clarify",
                "message": data.get("clarification_message")
            }
            
        sql_query = data.get("sql_query")
        if not sql_query:
            raise ValueError("No se recibió una consulta SQL válida de la IA.")
            
        # 2. Ejecutar SQL
        try:
            raw_data = SQLExecutor.execute(sql_query)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error al ejecutar query dinámico. SQL: {sql_query}\nDetalle: {str(e)}", exc_info=True)
            return {
                "action": "error",
                "message": "Hubo un problema al procesar los datos de tu consulta. Por favor, intenta de nuevo con otra pregunta o contacta a soporte si persiste."
            }
            
        # 3. Formatear para Recharts enviando los datos a FastAPI
        chart_url = DynamicReportsBFFService.get_ai_service_url("/format-chart")
        chart_response = requests.post(chart_url, json={"raw_data": raw_data, "prompt": prompt})
        chart_response.raise_for_status()
        chart_data = chart_response.json()
        
        return {
            "action": "success",
            "chart_data": chart_data,
            "raw_data": raw_data
        }
