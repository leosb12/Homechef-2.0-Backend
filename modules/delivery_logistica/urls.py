from django.urls import path

from .views import (
    delivery_accept_view,
    delivery_active_view,
    delivery_arrived_chef_view,
    delivery_assigned_view,
    delivery_current_location_view,
    delivery_delivered_view,
    delivery_detail_view,
    delivery_reject_view,
    delivery_incident_resolve_view,
    delivery_incidents_collection_view,
    delivery_location_ping_view,
    delivery_picked_up_view,
    delivery_route_refresh_view,
    delivery_route_snapshot_view,
    module_home,
)

urlpatterns = [
    path("", module_home),
    path("delivery/assigned/", delivery_assigned_view),
    path("delivery/active/", delivery_active_view),
    path("delivery/<str:assignment_id>/", delivery_detail_view),
    path("delivery/<str:assignment_id>/accept/", delivery_accept_view),
    path("delivery/<str:assignment_id>/reject/", delivery_reject_view),
    path("delivery/<str:assignment_id>/arrived-chef/", delivery_arrived_chef_view),
    path("delivery/<str:assignment_id>/picked-up/", delivery_picked_up_view),
    path("delivery/<str:assignment_id>/delivered/", delivery_delivered_view),
    path("delivery/<str:assignment_id>/location-pings/", delivery_location_ping_view),
    path("delivery/<str:assignment_id>/current-location/", delivery_current_location_view),
    path("delivery/<str:assignment_id>/route/", delivery_route_snapshot_view),
    path("delivery/<str:assignment_id>/route/refresh/", delivery_route_refresh_view),
    path("delivery/<str:assignment_id>/incidents/", delivery_incidents_collection_view),
    path("delivery/<str:assignment_id>/incidents/<str:incident_id>/resolve/", delivery_incident_resolve_view),
]
