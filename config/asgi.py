import os
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from modules.delivery_logistica.routing import websocket_urlpatterns
from shared.security.channels_auth import SupabaseTokenAuthMiddleware
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
django_asgi_app = get_asgi_application()
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": SupabaseTokenAuthMiddleware(URLRouter(websocket_urlpatterns)),
    }
)
