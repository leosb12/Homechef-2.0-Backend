from django.urls import path

from .views import (
    delivery_active_order_detail_view,
    delivery_active_orders_collection_view,
    delivery_driver_status_update_view,
    delivery_drivers_collection_view,
    module_home,
    notification_devices_register_view,
    notification_devices_unregister_view,
    notifications_collection_view,
    notifications_mark_all_read_view,
    notifications_mark_read_view,
)

urlpatterns = [
    path("", module_home),
    path("delivery-orders/active/", delivery_active_orders_collection_view),
    path("delivery-orders/active/<str:order_id>/", delivery_active_order_detail_view),
    path("delivery-drivers/", delivery_drivers_collection_view),
    path("delivery-drivers/<str:user_id>/status/", delivery_driver_status_update_view),
    path("notifications/", notifications_collection_view),
    path("notifications/read-all/", notifications_mark_all_read_view),
    path("notifications/<str:notification_id>/read/", notifications_mark_read_view),
    path("notifications/devices/register/", notification_devices_register_view),
    path("notifications/devices/unregister/", notification_devices_unregister_view),
]
