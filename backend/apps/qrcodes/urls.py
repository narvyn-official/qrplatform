from django.urls import path
from apps.qrcodes import views

app_name = "qrcodes"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("qrcodes/", views.qrcode_list, name="list"),
    path("qrcodes/create/", views.qrcode_create, name="create"),
    path("qrcodes/<uuid:pk>/", views.qrcode_detail, name="detail"),
    path("qrcodes/<uuid:pk>/edit/", views.qrcode_edit, name="edit"),
    path("qrcodes/<uuid:pk>/delete/", views.qrcode_delete, name="delete"),
    path("qrcodes/<uuid:pk>/download/<str:fmt>/", views.qrcode_download, name="download"),
]
