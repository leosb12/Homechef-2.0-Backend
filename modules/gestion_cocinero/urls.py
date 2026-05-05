from django.urls import path
from .views import (
    chef_availability_view,
    chef_dashboard_view,
    chef_dishes_collection_view,
    chef_dishes_item_view,
    chef_menu_view,
    chef_profile_view,
    chef_profile_location_view,
    module_home,
)

urlpatterns = [
    path('', module_home),
    path('profile/', chef_profile_view),
    path('profile/location/', chef_profile_location_view),
    path('availability/', chef_availability_view),
    path('dashboard/', chef_dashboard_view),
    path('dishes/', chef_dishes_collection_view),
    path('dishes/<str:dish_id>/', chef_dishes_item_view),
    path('menu/', chef_menu_view),
]
