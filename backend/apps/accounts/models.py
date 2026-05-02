"""
Custom user model and related account models.
"""
import uuid
import secrets
import hashlib
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils.translation import gettext_lazy as _
from django.utils import timezone


class UserManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email).lower()
        extra_fields.setdefault("is_active", False)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("role", "admin")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):

    class Role(models.TextChoices):
        ADMIN = "admin", _("Admin")
        USER = "user", _("User")
        PRO = "pro", _("Pro User")
        ENTERPRISE = "enterprise", _("Enterprise")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_("email address"), unique=True, db_index=True)
    full_name = models.CharField(_("full name"), max_length=150, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.USER)

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)

    date_joined = models.DateTimeField(default=timezone.now)
    last_activity = models.DateTimeField(null=True, blank=True)

    totp_secret = models.CharField(max_length=32, blank=True)
    is_2fa_enabled = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "accounts_user"
        verbose_name = _("user")
        verbose_name_plural = _("users")
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["role", "is_active"]),
        ]

    def __str__(self):
        return self.email

    @property
    def display_name(self):
        return self.full_name or self.email.split("@")[0]

    @property
    def is_pro(self):
        return self.role in (self.Role.PRO, self.Role.ENTERPRISE, self.Role.ADMIN)

    @property
    def plan_limits(self):
        unlimited = dict(
            max_qr=-1, max_scans=-1, logo=True, custom_shapes=True,
            utm=True, export=True, api=True, scheduled=True, clone=True,
        )
        if self.role in (self.Role.ADMIN, self.Role.ENTERPRISE):
            return unlimited
        if self.role == self.Role.PRO:
            return dict(
                max_qr=100, max_scans=50_000, logo=True, custom_shapes=True,
                utm=True, export=True, api=True, scheduled=True, clone=True,
            )
        return dict(
            max_qr=5, max_scans=1_000, logo=False, custom_shapes=False,
            utm=False, export=False, api=False, scheduled=False, clone=True,
        )

    def update_last_activity(self):
        User.objects.filter(pk=self.pk).update(last_activity=timezone.now())


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    company = models.CharField(max_length=200, blank=True)
    website = models.URLField(blank=True)
    timezone = models.CharField(max_length=50, default="UTC")
    phone = models.CharField(max_length=20, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    brand_name = models.CharField(max_length=100, blank=True)
    brand_logo = models.ImageField(upload_to="brands/", null=True, blank=True)
    brand_color = models.CharField(max_length=7, default="#6366F1")
    email_weekly_report = models.BooleanField(default=True)
    email_scan_alerts = models.BooleanField(default=False)
    scan_alert_threshold = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_userprofile"

    def __str__(self):
        return f"Profile({self.user.email})"


class EmailVerificationToken(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="email_token")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "accounts_email_verification"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(48)
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(hours=24)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"EmailToken({self.user.email})"


class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_tokens")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        db_table = "accounts_password_reset"
        indexes = [models.Index(fields=["token", "is_used"])]

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(48)
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(hours=2)
        super().save(*args, **kwargs)

    @property
    def is_valid(self):
        return not self.is_used and timezone.now() < self.expires_at

    def __str__(self):
        return f"ResetToken({self.user.email})"


class APIKey(models.Model):

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        REVOKED = "revoked", _("Revoked")
        EXPIRED = "expired", _("Expired")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=100)
    key_prefix = models.CharField(max_length=8, db_index=True)
    key_hash = models.CharField(max_length=128)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    scopes = models.JSONField(default=list)
    rate_limit = models.PositiveIntegerField(default=10000)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_api_key"
        indexes = [models.Index(fields=["key_prefix", "status"])]

    def __str__(self):
        return f"APIKey({self.name})"

    @property
    def is_active(self):
        if self.status != self.Status.ACTIVE:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

    @classmethod
    def generate(cls, user, name, scopes=None):
        raw_key = "qrf_" + secrets.token_urlsafe(40)
        prefix = raw_key[:8]
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        obj = cls.objects.create(
            user=user,
            name=name,
            key_prefix=prefix,
            key_hash=key_hash,
            scopes=scopes or ["qr:read", "qr:write"],
        )
        return obj, raw_key


class AuditLog(models.Model):

    class Action(models.TextChoices):
        LOGIN = "login", _("Login")
        LOGOUT = "logout", _("Logout")
        LOGIN_FAILED = "login_failed", _("Failed Login")
        PASSWORD_CHANGE = "password_change", _("Password Change")
        PASSWORD_RESET = "password_reset", _("Password Reset")
        EMAIL_VERIFY = "email_verify", _("Email Verified")
        API_KEY_CREATE = "api_key_create", _("API Key Created")
        API_KEY_REVOKE = "api_key_revoke", _("API Key Revoked")
        QR_CREATE = "qr_create", _("QR Created")
        QR_UPDATE = "qr_update", _("QR Updated")
        QR_DELETE = "qr_delete", _("QR Deleted")
        PROFILE_UPDATE = "profile_update", _("Profile Updated")

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="audit_logs")
    action = models.CharField(max_length=30, choices=Action.choices, db_index=True)
    ip_address = models.GenericIPAddressField(null=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "accounts_audit_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "action"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"AuditLog({self.action})"
