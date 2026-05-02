from django.urls import path
from apps.qrcodes.views import qr_redirect

urlpatterns = [
    path("<str:short_code>/", qr_redirect, name="qr_redirect"),
]
