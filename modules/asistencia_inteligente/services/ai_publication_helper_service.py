from ..ai_client import AIClient

class AIPublicationHelperService:
    def run(self, payload):
        return AIClient().post('/api/v1/ai/publication-assistant/generate', payload)
