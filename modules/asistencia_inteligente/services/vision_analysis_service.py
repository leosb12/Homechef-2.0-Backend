from ..ai_client import AIClient

class VisionAnalysisService:
    def run(self, payload):
        return AIClient().post('/internal/ai/analyze-image', payload)
