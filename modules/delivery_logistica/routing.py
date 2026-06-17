from django.urls import re_path

from .consumers import DeliveryAssignmentConsumer, DeliveryDashboardConsumer

websocket_urlpatterns = [
    re_path(
        r"ws/logistics/delivery/dashboard/$",
        DeliveryDashboardConsumer.as_asgi(),
    ),
    re_path(
        r"ws/logistics/delivery/(?P<assignment_id>[^/]+)/$",
        DeliveryAssignmentConsumer.as_asgi(),
    ),
]
