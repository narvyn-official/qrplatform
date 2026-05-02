from django.urls import path
from apps.barcodes import views

app_name = "barcodes"

urlpatterns = [
    path("", views.barcode_list, name="list"),
    path("create/", views.barcode_create, name="create"),
    path("<uuid:pk>/", views.barcode_detail, name="detail"),
    path("<uuid:pk>/delete/", views.barcode_delete, name="delete"),
    path("<uuid:pk>/download/<str:fmt>/", views.barcode_download, name="download"),
    path("bulk/", views.bulk_barcode, name="bulk"),
]
