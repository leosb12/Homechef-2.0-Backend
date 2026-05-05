from django.urls import path
from .views import ai_module_home, cooking_assistant, analyze_image, suggest_production_price, publication_helper

urlpatterns = [
    path('', ai_module_home),
    path('cooking-assistant/', cooking_assistant),
    path('analyze-image/', analyze_image),
    path('suggest-production-price/', suggest_production_price),
    path('publication-helper/', publication_helper),
]
