from django.apps import AppConfig


class QRCodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.qrcodes"
    verbose_name = "QRCodes"

    def ready(self):
        pass
