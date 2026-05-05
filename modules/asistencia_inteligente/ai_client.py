import requests
from django.conf import settings

class AIClient:
    def __init__(self):
        self.base_url = settings.AI_SERVICE_URL
        self.token = settings.AI_SERVICE_TOKEN

    def post(self, path, payload):
        response = requests.post(
            f"{self.base_url}{path}",
            json=payload,
            headers={'X-AI-Service-Token': self.token},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()
