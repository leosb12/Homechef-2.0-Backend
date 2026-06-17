from django.urls import path
from .views import (
    add_dish_to_cart,
    chef_public_profile,
    chef_reputation,
    client_explore_dashboard,
    create_chef_review,
    create_dish_review,
    dish_detail,
    favorites_view,
    module_home,
    preferences_view,
    public_dashboard,
    remove_favorite,
)
from modules.confianza_administracion_seguridad.views import report_publication

urlpatterns = [
    path('', module_home),
    path('public-dashboard/', public_dashboard),
    path('client/explore/', client_explore_dashboard),
    path('client/dishes/<str:dish_id>/detail/', dish_detail),
    path('client/dishes/<str:dish_id>/add-to-cart/', add_dish_to_cart),
    path('client/dishes/<str:dish_id>/reviews/', create_dish_review),
    path('client/dishes/<str:dish_id>/report/', report_publication, name='dish-report'),
    path('client/favorites/', favorites_view),
    path('client/favorites/<str:favorite_type>/<str:ref_id>/', remove_favorite),
    path('client/preferences/', preferences_view),
    path('client/chefs/<str:chef_id>/profile/', chef_public_profile),
    path('client/chefs/<str:chef_id>/reputation/', chef_reputation),
    path('client/chefs/<str:chef_id>/reviews/', create_chef_review),
]
