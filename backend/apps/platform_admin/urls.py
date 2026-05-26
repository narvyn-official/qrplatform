from django.urls import path

from apps.platform_admin import views

app_name = "platform_admin"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("users/", views.users, name="users"),
    path("users/<uuid:user_id>/", views.user_detail, name="user_detail"),
    path("qrcodes/", views.qrcodes, name="qrcodes"),
    path("qrcodes/<uuid:qr_id>/", views.qrcode_detail, name="qrcode_detail"),
    path("certificates/", views.certificates, name="certificates"),
    path("certificates/<uuid:certificate_id>/", views.certificate_detail, name="certificate_detail"),
    path("verifications/", views.verifications, name="verifications"),
    path("verifications/<uuid:verification_id>/", views.verification_detail, name="verification_detail"),
    path("reports/", views.reports, name="reports"),
    path("reports/<uuid:report_id>/", views.report_detail, name="report_detail"),
    path("orders/", views.orders, name="orders"),
    path("audit-logs/", views.audit_logs, name="audit_logs"),
]
