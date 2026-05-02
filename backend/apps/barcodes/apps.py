from django.apps import AppConfig


class BarcodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.barcodes"
    verbose_name = "Barcodes"

    def ready(self):
        pass
