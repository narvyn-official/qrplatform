from django.urls import path
from apps.core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("pricing/", views.pricing, name="pricing"),
    path("health/", views.health_check, name="health"),
]
