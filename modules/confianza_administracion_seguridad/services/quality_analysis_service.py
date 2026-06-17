import logging
from django.utils import timezone
from modules.asistencia_inteligente.ai_client import AIClient

logger = logging.getLogger(__name__)

class QualityAnalysisService:
    def analyze_publication_quality(self, dish) -> dict:
        """
        Envía la publicación al microservicio de IA para evaluar su calidad.
        """
        # Extraer una categoría preliminar de los tags si existen, de lo contrario usar 'Almuerzos'
        category = "Almuerzos"
        if isinstance(dish.tags, list) and len(dish.tags) > 0:
            category = dish.tags[0]

        # Convert ingredients to a list of strings
        ingredients_list = []
        if isinstance(dish.ingredients, list):
            for ing in dish.ingredients:
                if isinstance(ing, dict):
                    ingredients_list.append(str(ing.get("name", str(ing))))
                else:
                    ingredients_list.append(str(ing))

        payload = {
            "publication_id": str(dish.id),
            "chef_id": str(dish.chef.id),
            "title": dish.name,
            "description": dish.description or "",
            "ingredients": ingredients_list,
            "price": float(dish.price),
            "category": category,
            "image_url": dish.image_url or "",
            "reports_count": getattr(dish, "reported_count", 0)
        }

        try:
            logger.info(f"[QualityAnalysisService] Enviando plato {dish.id} a análisis de calidad IA...")
            client = AIClient()
            result = client.post("/api/v1/ai/publication-quality/analyze", payload)
            
            # Sincronizar el resultado en Django
            self.sync_quality_result(dish, result)
            return {"success": True, "data": result}
        except Exception as e:
            logger.exception(f"[QualityAnalysisService] Error al llamar al microservicio IA para plato {dish.id}: {e}")
            self.handle_quality_service_error(dish)
            return {"success": False, "error": str(e)}

    def sync_quality_result(self, dish, result: dict):
        """
        Actualiza los campos de calidad del plato en Django con la respuesta de FastAPI.
        """
        dish.ia_risk_score = result.get("risk_score")
        dish.revision_status = result.get("status_suggested", "pendiente_revision_ia")
        dish.ia_quality_review_id = result.get("review_id", "")
        dish.ia_quality_reasons = result.get("reasons", [])
        dish.ia_quality_recommendation = result.get("recommendation", "")
        dish.last_quality_analysis_at = timezone.now()
        
        # Ocultamiento automático opcional si el riesgo es crítico (>80)
        # Si se oculta temporalmente, cambiamos el status del plato a 'paused'
        # para que no esté visible en el marketplace hasta que sea moderado.
        if dish.revision_status == "oculta_temporalmente":
            dish.status = "paused"
            
        dish.save(update_fields=[
            "ia_risk_score",
            "revision_status",
            "ia_quality_review_id",
            "ia_quality_reasons",
            "ia_quality_recommendation",
            "last_quality_analysis_at",
            "status"
        ])
        logger.info(f"[QualityAnalysisService] Plato {dish.id} sincronizado. Riesgo: {dish.ia_risk_score}, Estado: {dish.revision_status}")

    def handle_quality_service_error(self, dish):
        """
        Fallback si el microservicio de IA falla.
        """
        dish.ia_risk_score = None
        dish.revision_status = "requiere_revision"
        dish.ia_quality_reasons = ["No se pudo completar análisis IA. Revisión manual requerida."]
        dish.ia_quality_recommendation = "Por favor, un administrador debe revisar manualmente el contenido de esta publicación."
        dish.last_quality_analysis_at = timezone.now()
        dish.save(update_fields=[
            "ia_risk_score",
            "revision_status",
            "ia_quality_reasons",
            "ia_quality_recommendation",
            "last_quality_analysis_at"
        ])
        logger.info(f"[QualityAnalysisService] Plato {dish.id} marcado con requiere_revision por fallo en el microservicio.")

    def register_admin_decision(self, dish_id: str, admin_id: str, decision: str, comment: str):
        """
        Registra la decisión de moderación del administrador en el microservicio de IA.
        """
        payload = {
            "publication_id": str(dish_id),
            "admin_id": str(admin_id),
            "decision": decision,
            "comment": comment
        }
        try:
            client = AIClient()
            client.post("/api/v1/ai/publication-quality/admin/decision", payload)
            logger.info(f"[QualityAnalysisService] Decisión de admin registrada para plato {dish_id} en FastAPI.")
        except Exception as e:
            logger.error(f"[QualityAnalysisService] Error registrando decisión de admin en FastAPI para plato {dish_id}: {e}")
