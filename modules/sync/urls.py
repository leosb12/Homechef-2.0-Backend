from django.urls import path

from modules.sync.views import sync_view

urlpatterns = [
    path("", sync_view),
]
