from django.urls import path

from modules.storage_uploads.views import upload_file_view


urlpatterns = [
    path("", upload_file_view),
]
