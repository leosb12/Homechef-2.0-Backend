from django.urls import path
from .views import (
    change_password,
    login_view,
    logout_view,
    module_home,
    profile_view,
    recover_password_confirm,
    recover_password_request,
    register_view,
)

urlpatterns = [
    path("", module_home),
    path("register/", register_view),
    path("login/", login_view),
    path("logout/", logout_view),
    path("recover-password/request/", recover_password_request),
    path("recover-password/confirm/", recover_password_confirm),
    path("profile/", profile_view),
    path("profile/change-password/", change_password),
]
