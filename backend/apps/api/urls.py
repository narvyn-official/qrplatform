"""
API v1 URL routing.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from apps.api.views import (
    QRCodeViewSet, BarcodeViewSet, AnalyticsViewSet,
    UserViewSet, APIKeyViewSet,
)
from apps.accounts.serializers import CustomTokenObtainPairView

app_name = "api_v1"

router = DefaultRouter()
router.register("qrcodes", QRCodeViewSet, basename="qrcodes")
router.register("barcodes", BarcodeViewSet, basename="barcodes")
router.register("analytics", AnalyticsViewSet, basename="analytics")
router.register("users", UserViewSet, basename="users")
router.register("api-keys", APIKeyViewSet, basename="api-keys")

urlpatterns = [
    # JWT auth
    path("auth/token/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/token/verify/", TokenVerifyView.as_view(), name="token_verify"),

    # Resource routes
    path("", include(router.urls)),
]
