from django.urls import re_path

from .consumers import DeliveryAssignmentConsumer

websocket_urlpatterns = [
    re_path(
        r"ws/logistics/delivery/(?P<assignment_id>[^/]+)/$",
        DeliveryAssignmentConsumer.as_asgi(),
    ),
]
