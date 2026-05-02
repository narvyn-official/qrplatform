"""
QR Code models — static, dynamic, campaigns, and expiration logic.
"""
import uuid
import shortuuid
from django.db import models
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.core.validators import MinValueValidator

User = get_user_model()


def qr_image_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1]
    return f"qrcodes/{instance.user.id}/{instance.short_code}.{ext}"


def qr_logo_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1]
    return f"qr_logos/{instance.user.id}/{uuid.uuid4().hex}.{ext}"


class QRCodeCampaign(models.Model):
    """Group QR codes under a campaign for bulk analytics."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campaigns")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    tags = models.JSONField(default=list)
    color = models.CharField(max_length=7, default="#6366F1")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qrcodes_campaign"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self):
        return f"Campaign({self.name})"

    @property
    def total_scans(self):
        return sum(qr.total_scans for qr in self.qrcodes.all())


class QRCode(models.Model):

    class QRType(models.TextChoices):
        URL = "url", _("URL")
        DYNAMIC = "dynamic", _("Dynamic URL")
        WHATSAPP = "whatsapp", _("WhatsApp")
        VCARD = "vcard", _("vCard / Contact")
        WIFI = "wifi", _("WiFi")
        TEXT = "text", _("Plain Text")
        EMAIL = "email", _("Email")
        SMS = "sms", _("SMS")

    class DotStyle(models.TextChoices):
        SQUARE = "square", _("Square")
        ROUNDED = "rounded", _("Rounded")
        DOTS = "dots", _("Dots")
        CLASSY = "classy", _("Classy")
        CLASSY_ROUNDED = "classy_rounded", _("Classy Rounded")
        EXTRA_ROUNDED = "extra_rounded", _("Extra Rounded")

    class CornerStyle(models.TextChoices):
        SQUARE = "square", _("Square")
        DOT = "dot", _("Dot")
        EXTRA_ROUNDED = "extra_rounded", _("Extra Rounded")

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        PAUSED = "paused", _("Paused")
        EXPIRED = "expired", _("Expired")
        DELETED = "deleted", _("Deleted")

    # Identity
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="qrcodes", db_index=True)
    campaign = models.ForeignKey(
        QRCodeCampaign, on_delete=models.SET_NULL, null=True, blank=True, related_name="qrcodes"
    )
    short_code = models.CharField(max_length=12, unique=True, db_index=True)
    name = models.CharField(max_length=200)
    qr_type = models.CharField(max_length=20, choices=QRType.choices, default=QRType.URL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    tags = models.JSONField(default=list)

    # Content
    content = models.TextField()          # static: final encoded content; dynamic: stores redirect target
    destination_url = models.URLField(max_length=2000, blank=True)  # dynamic only — editable

    # Design / Customization
    foreground_color = models.CharField(max_length=7, default="#000000")
    background_color = models.CharField(max_length=7, default="#FFFFFF")
    dot_style = models.CharField(max_length=20, choices=DotStyle.choices, default=DotStyle.SQUARE)
    corner_style = models.CharField(max_length=20, choices=CornerStyle.choices, default=CornerStyle.SQUARE)
    logo = models.ImageField(upload_to=qr_logo_path, null=True, blank=True)
    logo_size_ratio = models.FloatField(default=0.2, validators=[MinValueValidator(0.1)])
    frame_text = models.CharField(max_length=50, blank=True, default="")  # e.g. "Scan Me"
    frame_color = models.CharField(max_length=7, default="#000000")
    error_correction = models.CharField(
        max_length=1,
        choices=[("L", "Low"), ("M", "Medium"), ("Q", "Quartile"), ("H", "High")],
        default="M",
    )
    qr_size = models.PositiveIntegerField(default=300)  # pixels

    # Generated images
    image_png = models.ImageField(upload_to=qr_image_path, null=True, blank=True)
    image_svg = models.TextField(blank=True)  # SVG stored as text
    image_pdf = models.FileField(upload_to=qr_image_path, null=True, blank=True)

    # Expiration
    expires_at = models.DateTimeField(null=True, blank=True)
    scan_limit = models.PositiveIntegerField(null=True, blank=True)  # None = unlimited

    # Password protection
    is_password_protected = models.BooleanField(default=False)
    access_password_hash = models.CharField(max_length=128, blank=True)

    # Analytics counters (denormalized for speed)
    total_scans = models.PositiveBigIntegerField(default=0, db_index=True)
    unique_scans = models.PositiveBigIntegerField(default=0)
    last_scanned_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qrcodes_qrcode"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "qr_type"]),
            models.Index(fields=["short_code"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"QR({self.name} — {self.qr_type})"

    def save(self, *args, **kwargs):
        if not self.short_code:
            self.short_code = self._generate_unique_short_code()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_unique_short_code(length=8):
        while True:
            code = shortuuid.ShortUUID(alphabet="abcdefghijklmnopqrstuvwxyz0123456789").random(length)
            if not QRCode.objects.filter(short_code=code).exists():
                return code

    @property
    def redirect_url(self):
        from django.conf import settings
        return f"{settings.QR_REDIRECT_BASE}{self.short_code}"

    @property
    def is_dynamic(self):
        return self.qr_type == self.QRType.DYNAMIC

    @property
    def is_expired(self):
        if self.expires_at and timezone.now() > self.expires_at:
            return True
        if self.scan_limit and self.total_scans >= self.scan_limit:
            return True
        return False

    @property
    def encoded_content(self):
        """Return actual content to embed in QR image."""
        if self.is_dynamic:
            return self.redirect_url
        return self.content

    def increment_scan(self, is_unique=False):
        """Thread-safe counter increment using F expressions."""
        from django.db.models import F
        updates = {"total_scans": F("total_scans") + 1, "last_scanned_at": timezone.now()}
        if is_unique:
            updates["unique_scans"] = F("unique_scans") + 1
        QRCode.objects.filter(pk=self.pk).update(**updates)

    def set_access_password(self, raw_password):
        import hashlib
        self.access_password_hash = hashlib.sha256(raw_password.encode()).hexdigest()

    def check_access_password(self, raw_password):
        import hashlib
        return self.access_password_hash == hashlib.sha256(raw_password.encode()).hexdigest()


class QRScanEvent(models.Model):
    """Raw scan event — one row per scan, processed asynchronously."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qrcode = models.ForeignKey(QRCode, on_delete=models.CASCADE, related_name="scan_events", db_index=True)

    # Request metadata
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    ip_address = models.GenericIPAddressField(null=True)
    user_agent_raw = models.TextField(blank=True)

    # Parsed device info (populated by Celery task)
    device_type = models.CharField(max_length=20, blank=True)  # mobile/desktop/tablet/bot
    os_family = models.CharField(max_length=50, blank=True)
    browser_family = models.CharField(max_length=50, blank=True)

    # Geo info (populated by Celery task)
    country_code = models.CharField(max_length=2, blank=True, db_index=True)
    country_name = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    # Deduplication
    fingerprint = models.CharField(max_length=64, db_index=True)  # hash(ip+ua+date)
    is_unique = models.BooleanField(default=False)

    # Referrer
    referer = models.URLField(max_length=2000, blank=True)

    # Processing
    is_processed = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "qrcodes_scan_event"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["qrcode", "timestamp"]),
            models.Index(fields=["qrcode", "country_code"]),
            models.Index(fields=["qrcode", "device_type"]),
            models.Index(fields=["fingerprint"]),
            models.Index(fields=["is_processed"]),
        ]

    def __str__(self):
        return f"Scan({self.qrcode.short_code} @ {self.timestamp})"
