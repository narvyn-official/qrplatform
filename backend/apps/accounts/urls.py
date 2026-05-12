from django.urls import path
from apps.accounts import views
from apps.accounts import billing_views

app_name = "accounts"

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("login/", views.login_view, name="login"),
    path("google/", views.google_login, name="google_login"),
    path("google/callback/", views.google_callback, name="google_callback"),
    path("logout/", views.logout_view, name="logout"),
    path("resend-verification/", views.resend_verification, name="resend_verification"),
    path("verify/<str:token>/", views.verify_email, name="verify_email"),
    path("forgot-password/", views.forgot_password, name="forgot_password"),
    path("reset-password/<str:token>/", views.reset_password, name="reset_password"),
    path("profile/", views.profile, name="profile"),
    path("change-password/", views.change_password, name="change_password"),
    path("api-keys/create/", views.create_api_key, name="create_api_key"),
    path("api-keys/<uuid:key_id>/revoke/", views.revoke_api_key, name="revoke_api_key"),
    path("billing/checkout/<str:plan_code>/", billing_views.checkout, name="billing_checkout"),
    path("billing/callback/", billing_views.billing_callback, name="billing_callback"),
    path("billing/webhook/paytm/", billing_views.paytm_webhook, name="paytm_webhook"),
]
