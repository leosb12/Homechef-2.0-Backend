from django.urls import path

from .views import (
    admin_platform_users_collection_view,
    admin_platform_user_toggle_block_view,
    admin_platform_pending_chefs_view,
    admin_platform_chef_validate_view,
    admin_platform_publications_view,
    admin_platform_publication_action_view,
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
    path("users/", admin_platform_users_collection_view),
    path("users/<str:user_id>/toggle-block/", admin_platform_user_toggle_block_view),
    path("chefs/pending/", admin_platform_pending_chefs_view),
    path("chefs/<str:chef_id>/validate/", admin_platform_chef_validate_view),
    path("publications/", admin_platform_publications_view),
    path("publications/<str:dish_id>/action/", admin_platform_publication_action_view),
]
