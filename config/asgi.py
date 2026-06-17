import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from shared.security.channels_auth import SupabaseTokenAuthMiddleware

django_asgi_app = get_asgi_application()

from modules.delivery_logistica.routing import websocket_urlpatterns as delivery_websocket_urlpatterns
from modules.pedidos_checkout_pagos.routing import websocket_urlpatterns as order_websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": SupabaseTokenAuthMiddleware(
            URLRouter([*delivery_websocket_urlpatterns, *order_websocket_urlpatterns])
        ),
    }
)
