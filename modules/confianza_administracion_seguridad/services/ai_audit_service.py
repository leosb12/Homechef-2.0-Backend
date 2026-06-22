from modules.confianza_administracion_seguridad.services.mongo_ai_audit_repository import MongoAIAuditRepository


class AIAuditService:
    def __init__(self, repository=None):
        self.repository = repository or MongoAIAuditRepository()

    def list_collections(self):
        return self.repository.list_collections()

    def list_events(self, query_params):
        return self.repository.list_events(query_params)

    def summary(self, query_params):
        return self.repository.summary(query_params)

    def get_event(self, event_id):
        return self.repository.get_event(event_id)

    def export_events(self, query_params):
        return self.repository.export_events(query_params)
