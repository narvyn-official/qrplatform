from django.urls import path
from apps.qrcodes import views

app_name = "qrcodes"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("qrcodes/", views.qrcode_list, name="list"),
    path("qrcodes/create/", views.qrcode_create, name="create"),
    path("qrcodes/export/zip/", views.qrcode_export_zip, name="export_zip"),
    path("scan/", views.qrcode_scan, name="scan"),
    path("qrcodes/<uuid:pk>/", views.qrcode_detail, name="detail"),
    path("qrcodes/<uuid:pk>/edit/", views.qrcode_edit, name="edit"),
    path("qrcodes/<uuid:pk>/delete/", views.qrcode_delete, name="delete"),
    path("qrcodes/<uuid:pk>/clone/", views.qrcode_clone, name="clone"),
    path("qrcodes/<uuid:pk>/regenerate/", views.qrcode_regenerate, name="regenerate"),
    path("qrcodes/<uuid:pk>/analytics.csv", views.qrcode_analytics_csv, name="analytics_csv"),
    path("qrcodes/<uuid:pk>/download/<str:fmt>/", views.qrcode_download, name="download"),
]
