from django.urls import re_path

from .consumers import OrderTrackingConsumer

websocket_urlpatterns = [
    re_path(
        r"ws/orders/tracking/(?P<order_id>[^/]+)/$",
        OrderTrackingConsumer.as_asgi(),
    ),
]
