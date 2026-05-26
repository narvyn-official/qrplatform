from django.urls import path
from apps.qrcodes import views

app_name = "qrcodes"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("qrcodes/", views.qrcode_list, name="list"),
    path("qrcodes/create/", views.qrcode_create, name="create"),
    path("templates/", views.qrcode_template_gallery, name="templates"),
    path("templates/<slug:slug>/", views.qrcode_template_create, name="template_create"),
    path("certificates/", views.certificate_list, name="certificate_list"),
    path("certificates/new/", views.certificate_new, name="certificate_new"),
    path("certificates/bulk-upload/", views.certificate_bulk_upload, name="certificate_bulk_upload"),
    path("certificates/<uuid:pk>/", views.certificate_detail, name="certificate_detail"),
    path("certificates/<uuid:pk>/revoke/", views.certificate_revoke, name="certificate_revoke"),
    path("qrcodes/export/zip/", views.qrcode_export_zip, name="export_zip"),
    path("premium/", views.premium_studio, name="premium"),
    path("scan/", views.qrcode_scan, name="scan"),
    path("scan/safety-check/", views.qrcode_safety_check, name="safety_check"),
    path("scan/report/", views.qrcode_report_suspicious, name="report_suspicious"),
    path("qrcodes/<uuid:pk>/", views.qrcode_detail, name="detail"),
    path("qrcodes/<uuid:pk>/edit/", views.qrcode_edit, name="edit"),
    path("qrcodes/<uuid:pk>/preflight/", views.qrcode_preflight, name="preflight"),
    path("qrcodes/<uuid:pk>/pause-campaign/", views.qrcode_pause_campaign, name="pause_campaign"),
    path("qrcodes/<uuid:pk>/delete/", views.qrcode_delete, name="delete"),
    path("qrcodes/<uuid:pk>/clone/", views.qrcode_clone, name="clone"),
    path("qrcodes/<uuid:pk>/regenerate/", views.qrcode_regenerate, name="regenerate"),
    path("qrcodes/<uuid:pk>/analytics.csv", views.qrcode_analytics_csv, name="analytics_csv"),
    path("qrcodes/<uuid:pk>/download/<str:fmt>/", views.qrcode_download, name="download"),
]
