from django.contrib import admin
from apps.qrcodes.models import QRCode, QRScanEvent, QRCodeCampaign, QRSuspiciousReport, QRScannerHistory


@admin.register(QRCodeCampaign)
class QRCodeCampaignAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "is_active", "created_at"]
    search_fields = ["name", "user__email"]


@admin.register(QRCode)
class QRCodeAdmin(admin.ModelAdmin):
    list_display = ["name", "short_code", "qr_type", "status", "total_scans", "user", "created_at"]
    list_filter = ["qr_type", "status"]
    search_fields = ["name", "short_code", "user__email"]
    readonly_fields = ["short_code", "total_scans", "unique_scans", "last_scanned_at", "created_at"]


@admin.register(QRScanEvent)
class QRScanEventAdmin(admin.ModelAdmin):
    list_display = ["qrcode", "timestamp", "country_code", "device_type", "is_unique", "is_processed"]
    list_filter = ["device_type", "is_unique", "is_processed"]
    readonly_fields = [f.name for f in QRScanEvent._meta.get_fields() if hasattr(f, "name")]


@admin.register(QRSuspiciousReport)
class QRSuspiciousReportAdmin(admin.ModelAdmin):
    list_display = ["host", "reason", "risk_level", "status", "qrcode", "campaign", "reporter", "created_at"]
    list_filter = ["reason", "risk_level", "status", "created_at"]
    search_fields = ["host", "normalized_url", "reported_url", "scanned_value", "comment", "reporter__email", "qrcode__name", "campaign__name"]
    readonly_fields = ["created_at", "updated_at", "ip_address", "user_agent_raw", "safety_snapshot"]


@admin.register(QRScannerHistory)
class QRScannerHistoryAdmin(admin.ModelAdmin):
    list_display = ["user", "content_type", "domain", "risk_level", "scanned_at"]
    list_filter = ["content_type", "risk_level", "scanned_at"]
    search_fields = ["raw_content", "domain", "user__email"]
    readonly_fields = ["scanned_at"]
