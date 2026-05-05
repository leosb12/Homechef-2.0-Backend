from ..ai_client import AIClient

class CookingAssistantService:
    def run(self, payload):
        return AIClient().post('/internal/ai/cooking-assistant', payload)
