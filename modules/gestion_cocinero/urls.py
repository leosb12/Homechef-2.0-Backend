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
from .views.inventory_views import (
    chef_inventory_collection_view,
    chef_inventory_item_view,
)
from .views.finance_views import chef_finances_summary_view

urlpatterns = [
    path('', module_home),
    path('profile/', chef_profile_view),
    path('profile/location/', chef_profile_location_view),
    path('availability/', chef_availability_view),
    path('dashboard/', chef_dashboard_view),
    path('dishes/', chef_dishes_collection_view),
    path('dishes/<str:dish_id>/', chef_dishes_item_view),
    path('menu/', chef_menu_view),
    path('inventory/', chef_inventory_collection_view),
    path('inventory/<int:item_id>/', chef_inventory_item_view),
    path('finances/summary/', chef_finances_summary_view),
]
