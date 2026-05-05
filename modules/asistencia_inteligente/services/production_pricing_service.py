from ..ai_client import AIClient

class ProductionPricingService:
    def run(self, payload):
        return AIClient().post('/internal/ai/suggest-production-price', payload)
