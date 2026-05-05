from django.urls import path
from .views import module_home

urlpatterns = [path('', module_home)]
