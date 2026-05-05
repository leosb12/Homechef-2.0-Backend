from ..ai_client import AIClient

class AIPublicationHelperService:
    def run(self, payload):
        return AIClient().post('/internal/ai/publication-helper', payload)
