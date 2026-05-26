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
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.conf import settings

User = get_user_model()


def qr_image_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1]
    return f"qrcodes/{instance.user.id}/{instance.short_code}.{ext}"


def qr_logo_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1]
    return f"qr_logos/{instance.user.id}/{uuid.uuid4().hex}.{ext}"


def certificate_pdf_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower()
    return f"certificates/{instance.workspace_id}/{instance.verification_code or uuid.uuid4().hex}.{ext}"


class QRCodeCampaign(models.Model):
    """Group QR codes under a campaign for bulk analytics."""

    class RiskStatus(models.TextChoices):
        CLEAR = "clear", _("Clear")
        WATCH = "watch", _("Watch")
        ELEVATED = "elevated", _("Elevated")
        HIGH = "high", _("High")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campaigns")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    tags = models.JSONField(default=list)
    color = models.CharField(max_length=7, default="#6366F1")
    is_active = models.BooleanField(default=True)
    risk_status = models.CharField(max_length=20, choices=RiskStatus.choices, default=RiskStatus.CLEAR, db_index=True)
    suspicious_report_count = models.PositiveIntegerField(default=0)
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

    class OuterShape(models.TextChoices):
        SQUARE = "square", _("Square")
        ROUNDED = "rounded", _("Rounded Square")
        CIRCLE = "circle", _("Circle")
        CAPSULE = "capsule", _("Capsule")
        TICKET = "ticket", _("Ticket")
        APPLE = "apple", _("Apple")
        DUCK = "duck", _("Duck")
        SHIELD = "shield", _("Shield")
        BLOB = "blob", _("Blob")

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
    outer_shape = models.CharField(max_length=20, choices=OuterShape.choices, default=OuterShape.SQUARE)
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

    # Scheduled activation window (premium)
    scheduled_active_from = models.DateTimeField(null=True, blank=True)
    scheduled_active_until = models.DateTimeField(null=True, blank=True)

    # UTM auto-append (premium) — appended to redirect destination
    utm_params = models.JSONField(default=dict, blank=True)

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
        now = timezone.now()
        if self.expires_at and now > self.expires_at:
            return True
        if self.scan_limit and self.total_scans >= self.scan_limit:
            return True
        if self.scheduled_active_until and now > self.scheduled_active_until:
            return True
        return False

    @property
    def is_scheduled_inactive(self):
        """True before the scheduled activation window opens."""
        if self.scheduled_active_from and timezone.now() < self.scheduled_active_from:
            return True
        return False

    @property
    def encoded_content(self):
        """Content to embed in QR image.

        Every QR type encodes the platform redirect URL so scan counters,
        limits, schedules, passwords, and analytics work consistently. The
        redirect handler serves or forwards the stored content after counting.
        """
        return self.redirect_url

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


class QRDestinationRule(models.Model):
    """Smart redirect rule for device, language, country, time, or A/B routing."""

    class RuleType(models.TextChoices):
        DEVICE = "device", _("Device")
        LANGUAGE = "language", _("Language")
        COUNTRY = "country", _("Country")
        TIME = "time", _("Time Window")
        AB = "ab", _("A/B Test")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qrcode = models.ForeignKey(QRCode, on_delete=models.CASCADE, related_name="destination_rules")
    name = models.CharField(max_length=120)
    rule_type = models.CharField(max_length=20, choices=RuleType.choices, default=RuleType.DEVICE)
    match_value = models.CharField(
        max_length=120,
        blank=True,
        help_text="Examples: mobile, en, US, 09:00-17:00. A/B rules can leave this blank.",
    )
    destination_url = models.URLField(max_length=2000)
    weight = models.PositiveSmallIntegerField(default=50, validators=[MinValueValidator(1)])
    is_active = models.BooleanField(default=True)
    hits = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "qrcodes_destination_rule"
        ordering = ["rule_type", "name"]
        indexes = [models.Index(fields=["qrcode", "rule_type", "is_active"])]

    def __str__(self):
        return f"Rule({self.qrcode.short_code}: {self.name})"


class QRHealthCheck(models.Model):
    """Latest destination health and trust scan for a QR code."""

    class Status(models.TextChoices):
        UNKNOWN = "unknown", _("Unknown")
        OK = "ok", _("OK")
        WARNING = "warning", _("Warning")
        BROKEN = "broken", _("Broken")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qrcode = models.OneToOneField(QRCode, on_delete=models.CASCADE, related_name="health_check")
    checked_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    final_url = models.URLField(max_length=2000, blank=True)
    response_ms = models.PositiveIntegerField(default=0)
    is_safe = models.BooleanField(default=True)
    issue = models.TextField(blank=True)

    class Meta:
        db_table = "qrcodes_health_check"

    def __str__(self):
        return f"Health({self.qrcode.short_code}: {self.status})"


class QRLandingPage(models.Model):
    """Built-in mobile page used when a user does not have a website."""

    class Mode(models.TextChoices):
        LANDING = "landing", _("Landing Page")
        MENU = "menu", _("Menu")
        REVIEW = "review", _("Review")
        VCARD = "vcard", _("vCard")
        FILE = "file", _("File")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qrcode = models.OneToOneField(QRCode, on_delete=models.CASCADE, related_name="landing_page")
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.LANDING)
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)
    primary_label = models.CharField(max_length=80, default="Open")
    primary_url = models.URLField(max_length=2000, blank=True)
    items = models.JSONField(default=list, blank=True)
    published = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "qrcodes_landing_page"

    def __str__(self):
        return f"Landing({self.qrcode.short_code}: {self.title})"


class QRConversionEvent(models.Model):
    """Business conversion attached to a scan: click, form, review, purchase, etc."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qrcode = models.ForeignKey(QRCode, on_delete=models.CASCADE, related_name="conversion_events")
    event_type = models.CharField(max_length=60, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "qrcodes_conversion_event"
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["qrcode", "event_type", "timestamp"])]

    def __str__(self):
        return f"Conversion({self.qrcode.short_code}: {self.event_type})"


class QRSuspiciousReport(models.Model):
    """User-submitted safety report for a scanned QR payload."""

    class Reason(models.TextChoices):
        PHISHING = "phishing", _("Phishing")
        FAKE_PAYMENT = "fake_payment", _("Fake payment QR")
        WRONG_BUSINESS = "wrong_business", _("Wrong business")
        MALWARE = "malware", _("Malware/download")
        SPAM = "spam", _("Spam")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        REVIEWING = "reviewing", _("Reviewing")
        RESOLVED = "resolved", _("Resolved")
        DISMISSED = "dismissed", _("Dismissed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qrcode = models.ForeignKey(QRCode, on_delete=models.SET_NULL, null=True, blank=True, related_name="suspicious_reports")
    campaign = models.ForeignKey(QRCodeCampaign, on_delete=models.SET_NULL, null=True, blank=True, related_name="suspicious_reports")
    reporter = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="qr_reports")
    scanned_value = models.TextField()
    normalized_url = models.URLField(max_length=2000, blank=True)
    reported_url = models.URLField(max_length=2000, blank=True)
    host = models.CharField(max_length=255, blank=True, db_index=True)
    reason = models.CharField(max_length=30, choices=Reason.choices, default=Reason.OTHER, db_index=True)
    comment = models.TextField(blank=True)
    admin_notes = models.TextField(blank=True)
    risk_level = models.CharField(max_length=20, default="unknown", db_index=True)
    safety_snapshot = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent_raw = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qrcodes_suspicious_report"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["host", "status"]),
            models.Index(fields=["qrcode", "status"]),
            models.Index(fields=["campaign", "status"]),
            models.Index(fields=["reason", "created_at"]),
            models.Index(fields=["risk_level", "created_at"]),
        ]

    def __str__(self):
        return f"QR report({self.host or 'text'}: {self.status})"


class QRScannerHistory(models.Model):
    """Safe scanner history for signed-in users."""

    class RiskLevel(models.TextChoices):
        SAFE = "safe", _("Safe")
        CAUTION = "caution", _("Caution")
        RISKY = "risky", _("Risky")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="scanner_history", db_index=True)
    raw_content = models.TextField()
    content_type = models.CharField(max_length=20, db_index=True)
    domain = models.CharField(max_length=255, blank=True, db_index=True)
    risk_level = models.CharField(max_length=20, choices=RiskLevel.choices, db_index=True)
    scanned_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "qrcodes_scanner_history"
        ordering = ["-scanned_at"]
        indexes = [
            models.Index(fields=["user", "scanned_at"]),
            models.Index(fields=["user", "risk_level", "scanned_at"]),
        ]

    def __str__(self):
        return f"ScannerHistory({self.user_id}: {self.content_type}/{self.risk_level})"


class Certificate(models.Model):
    """Certificate record backed by a public verification QR code."""

    class Status(models.TextChoices):
        VALID = "valid", _("Valid")
        EXPIRED = "expired", _("Expired")
        REVOKED = "revoked", _("Revoked")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(User, on_delete=models.CASCADE, related_name="certificates", db_index=True)
    qrcode = models.OneToOneField(
        QRCode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificate",
    )
    recipient_name = models.CharField(max_length=180)
    title = models.CharField(max_length=220)
    issuer = models.CharField(max_length=220)
    issue_date = models.DateField()
    expiry_date = models.DateField(null=True, blank=True)
    certificate_id = models.CharField(max_length=120)
    verification_code = models.CharField(max_length=24, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.VALID, db_index=True)
    pdf_url = models.FileField(upload_to=certificate_pdf_path, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "qrcodes_certificate"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "certificate_id"], name="unique_certificate_id_per_workspace"),
        ]
        indexes = [
            models.Index(fields=["workspace", "status", "created_at"]),
            models.Index(fields=["verification_code"]),
            models.Index(fields=["certificate_id"]),
        ]

    def __str__(self):
        return f"Certificate({self.certificate_id}: {self.recipient_name})"

    def save(self, *args, **kwargs):
        if not self.verification_code:
            self.verification_code = self._generate_unique_verification_code()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.issue_date and self.issue_date > timezone.localdate():
            raise ValidationError({"issue_date": "Issue date cannot be in the future."})
        if self.issue_date and self.expiry_date and self.expiry_date < self.issue_date:
            raise ValidationError({"expiry_date": "Expiry date must be after the issue date."})

    @staticmethod
    def _generate_unique_verification_code(length=14):
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        while True:
            code = shortuuid.ShortUUID(alphabet=alphabet).random(length)
            if not Certificate.objects.filter(verification_code=code).exists():
                return code

    @property
    def is_expired(self):
        return bool(self.expiry_date and self.expiry_date < timezone.localdate())

    @property
    def effective_status(self):
        if self.status == self.Status.REVOKED:
            return self.Status.REVOKED
        if self.status == self.Status.EXPIRED or self.is_expired:
            return self.Status.EXPIRED
        return self.Status.VALID

    @property
    def status_label(self):
        return self.effective_status.title()

    @property
    def verify_path(self):
        return reverse("certificate_verify", kwargs={"code": self.verification_code})

    @property
    def verification_url(self):
        return f"{settings.PLATFORM_URL.rstrip('/')}{self.verify_path}"
