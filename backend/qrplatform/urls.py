"""
Root URL configuration.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
from apps.qrcodes import views as qrcode_views

urlpatterns = [
    # Admin
    path("admin/reports/", qrcode_views.admin_reports, name="admin_reports"),
    path("admin/reports/<uuid:report_id>/", qrcode_views.admin_report_detail, name="admin_report_detail"),
    path("admin/", admin.site.urls),

    # App routes
    path("", include("apps.core.urls", namespace="core")),
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("dashboard/", include("apps.qrcodes.urls", namespace="qrcodes")),
    path("barcodes/", include("apps.barcodes.urls", namespace="barcodes")),
    path("analytics/", include("apps.analytics.urls", namespace="analytics")),
    path("admin-panel/", include("apps.platform_admin.urls", namespace="platform_admin")),
    path("scanner/", qrcode_views.safe_scanner, name="safe_scanner"),
    path("scanner/result/", qrcode_views.safe_scanner_result, name="safe_scanner_result"),
    path("scanner/history/", qrcode_views.safe_scanner_history, name="safe_scanner_history"),
    path("verify/certificate/<str:code>/", qrcode_views.certificate_verify, name="certificate_verify"),
    path("p/<str:short_code>/", qrcode_views.public_landing_page, name="public_landing"),
    path("c/<str:short_code>/<str:event_type>/", qrcode_views.conversion_redirect, name="conversion_redirect"),

    # Dynamic QR redirect (short URL — must be kept last for /r/<code>)
    path("r/", include("apps.qrcodes.redirect_urls")),

    # REST API v1
    path("api/url/analyze", qrcode_views.url_analyze_api, name="url_analyze_api"),
    path("api/scanner/analyze", qrcode_views.scanner_analyze_api, name="scanner_analyze_api"),
    path("api/scanner/report", qrcode_views.scanner_report_api, name="scanner_report_api"),
    path("api/v1/", include("apps.api.urls", namespace="api_v1")),

    # OpenAPI docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]

if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [path("__debug__/", include(debug_toolbar.urls))] + urlpatterns
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom error handlers
handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"
