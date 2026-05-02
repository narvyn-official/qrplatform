from django.urls import path
from apps.analytics import views

app_name = "analytics"

urlpatterns = [
    path("<uuid:qr_id>/", views.analytics_detail, name="detail"),
]
