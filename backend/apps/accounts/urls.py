from django.urls import path
from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("verify/<str:token>/", views.verify_email, name="verify_email"),
    path("forgot-password/", views.forgot_password, name="forgot_password"),
    path("reset-password/<str:token>/", views.reset_password, name="reset_password"),
    path("profile/", views.profile, name="profile"),
    path("api-keys/create/", views.create_api_key, name="create_api_key"),
    path("api-keys/<uuid:key_id>/revoke/", views.revoke_api_key, name="revoke_api_key"),
]
