"""
QR Code forms.
"""
import re

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator
from django.utils import timezone
from apps.qrcodes.models import QRCode, QRCodeCampaign
from apps.qrcodes.utils import (
    build_email_content,
    build_sms_content,
    build_whatsapp_content,
)


HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s().-]{6,20}$")
HTTP_URL_VALIDATOR = URLValidator(schemes=("http", "https"))
EMAIL_VALIDATOR = EmailValidator()
MIN_QR_SIZE = 128
MAX_QR_SIZE = 2048
MAX_LOGO_SIZE_RATIO = 0.35


def normalize_http_url(value):
    url = (value or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    if url:
        HTTP_URL_VALIDATOR(url)
    return url


def validate_hex_color(value, field_label):
    color = (value or "").strip()
    if not HEX_COLOR_RE.match(color):
        raise ValidationError({field_label: "Use a valid hex color, for example #000000."})
    return color.upper()


class QRCodeForm(forms.ModelForm):
    destination_url = forms.CharField(
        required=False,
        max_length=2000,
        widget=forms.TextInput(attrs={"placeholder": "https://example.com"}),
    )
    access_password = forms.CharField(
        required=False,
        min_length=6,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "placeholder": "At least 6 characters",
            }
        ),
        help_text="Only needed when password protection is enabled or when changing the password.",
    )
    # UTM fields (rendered separately; stored into utm_params JSON)
    utm_source = forms.CharField(required=False, max_length=100,
                                  widget=forms.TextInput(attrs={"placeholder": "e.g. qrcode"}))
    utm_medium = forms.CharField(required=False, max_length=100,
                                  widget=forms.TextInput(attrs={"placeholder": "e.g. print"}))
    utm_campaign = forms.CharField(required=False, max_length=100,
                                    widget=forms.TextInput(attrs={"placeholder": "e.g. summer_promo"}))
    utm_content = forms.CharField(required=False, max_length=100,
                                   widget=forms.TextInput(attrs={"placeholder": "e.g. flyer_v1"}))

    class Meta:
        model = QRCode
        fields = [
            "name", "qr_type", "content", "destination_url",
            "campaign", "tags",
            "foreground_color", "background_color",
            "dot_style", "corner_style", "outer_shape",
            "logo", "logo_size_ratio",
            "frame_text", "frame_color",
            "error_correction", "qr_size",
            "expires_at", "scan_limit",
            "scheduled_active_from", "scheduled_active_until",
            "is_password_protected",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "QR Code name"}),
            "content": forms.Textarea(attrs={"rows": 3, "placeholder": "Enter content or URL"}),
            "destination_url": forms.URLInput(attrs={"placeholder": "https://example.com"}),
            "foreground_color": forms.TextInput(attrs={"type": "color"}),
            "background_color": forms.TextInput(attrs={"type": "color"}),
            "frame_color": forms.TextInput(attrs={"type": "color"}),
            "expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "scheduled_active_from": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "scheduled_active_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "tags": forms.HiddenInput(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user:
            self.fields["campaign"].queryset = QRCodeCampaign.objects.filter(user=user, is_active=True)
        self.fields["campaign"].required = False
        self.fields["content"].required = False
        self.fields["destination_url"].required = False
        self.fields["expires_at"].required = False
        self.fields["scan_limit"].required = False
        self.fields["tags"].required = False
        self.fields["scheduled_active_from"].required = False
        self.fields["scheduled_active_until"].required = False

        # Pre-populate UTM fields from existing utm_params JSON
        if self.instance and self.instance.pk and self.instance.utm_params:
            p = self.instance.utm_params
            self.fields["utm_source"].initial = p.get("utm_source", "")
            self.fields["utm_medium"].initial = p.get("utm_medium", "")
            self.fields["utm_campaign"].initial = p.get("utm_campaign", "")
            self.fields["utm_content"].initial = p.get("utm_content", "")

        input_class = (
            "block w-full px-3.5 py-2.5 border border-surface-200 rounded-lg text-sm "
            "focus:ring-2 focus:ring-primary-500 focus:border-transparent outline-none transition bg-white"
        )
        select_class = input_class
        checkbox_class = "rounded text-primary-600 border-surface-300 focus:ring-primary-500"

        for name, field in self.fields.items():
            if name in {"is_password_protected"}:
                field.widget.attrs.setdefault("class", checkbox_class)
            elif isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", checkbox_class)
            elif getattr(field.widget, "input_type", None) == "color":
                field.widget.attrs.setdefault(
                    "class",
                    "h-10 w-full rounded-lg border border-surface-200 cursor-pointer bg-white",
                )
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", select_class)
            elif not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault("class", input_class)

    def clean(self):
        cleaned = super().clean()
        qr_type = cleaned.get("qr_type")
        content = (cleaned.get("content") or "").strip()
        destination_url = (cleaned.get("destination_url") or "").strip()
        is_password_protected = cleaned.get("is_password_protected")
        access_password = cleaned.get("access_password")

        if qr_type == QRCode.QRType.DYNAMIC:
            if not destination_url:
                raise ValidationError({"destination_url": "Dynamic QR requires a destination URL."})
            try:
                destination_url = normalize_http_url(destination_url)
            except ValidationError:
                raise ValidationError({"destination_url": "Enter a valid http or https URL."})
            cleaned["destination_url"] = destination_url
            cleaned["content"] = destination_url

        elif qr_type == QRCode.QRType.URL:
            if not content:
                raise ValidationError({"content": "Website URL is required."})
            try:
                content = normalize_http_url(content)
            except ValidationError:
                raise ValidationError({"content": "Enter a valid http or https URL."})
            cleaned["content"] = content

        elif qr_type == QRCode.QRType.WHATSAPP:
            if not content:
                raise ValidationError({"content": "WhatsApp phone number is required."})
            if not PHONE_RE.match(content):
                raise ValidationError({"content": "Enter a valid WhatsApp phone number with country code."})
            if not content.startswith(("http://", "https://")):
                cleaned["content"] = build_whatsapp_content(content)

        elif qr_type == QRCode.QRType.EMAIL:
            if not content:
                raise ValidationError({"content": "Email content is required."})
            if not content.startswith("mailto:") and "@" in content and "\n" not in content:
                EMAIL_VALIDATOR(content)
                cleaned["content"] = build_email_content(content)

        elif qr_type == QRCode.QRType.SMS:
            if not content:
                raise ValidationError({"content": "SMS content is required."})
            if not content.startswith(("SMSTO:", "sms:")):
                parts = content.split("|", 1)
                phone = parts[0].strip()
                message = parts[1].strip() if len(parts) > 1 else ""
                if not PHONE_RE.match(phone):
                    raise ValidationError({"content": "Enter a valid SMS phone number."})
                cleaned["content"] = build_sms_content(phone, message)

        if not content and qr_type != QRCode.QRType.DYNAMIC:
            raise ValidationError({"content": "Content is required."})

        for field in ("foreground_color", "background_color", "frame_color"):
            if cleaned.get(field):
                cleaned[field] = validate_hex_color(cleaned[field], field)
        if cleaned.get("foreground_color") == cleaned.get("background_color"):
            raise ValidationError({"background_color": "Background color must be different from foreground color."})

        qr_size = cleaned.get("qr_size")
        if qr_size and not MIN_QR_SIZE <= qr_size <= MAX_QR_SIZE:
            raise ValidationError({"qr_size": f"QR size must be between {MIN_QR_SIZE} and {MAX_QR_SIZE} pixels."})

        logo_size_ratio = cleaned.get("logo_size_ratio")
        if logo_size_ratio and logo_size_ratio > MAX_LOGO_SIZE_RATIO:
            raise ValidationError({"logo_size_ratio": "Logo size must be 35% of the QR code or smaller."})

        expires_at = cleaned.get("expires_at")
        if expires_at and expires_at <= timezone.now():
            raise ValidationError({"expires_at": "Expiry must be in the future."})

        scheduled_active_from = cleaned.get("scheduled_active_from")
        scheduled_active_until = cleaned.get("scheduled_active_until")
        if scheduled_active_until and scheduled_active_until <= timezone.now():
            raise ValidationError({"scheduled_active_until": "Scheduled end must be in the future."})
        if scheduled_active_from and scheduled_active_until and scheduled_active_until <= scheduled_active_from:
            raise ValidationError({"scheduled_active_until": "Scheduled end must be after the start time."})

        scan_limit = cleaned.get("scan_limit")
        if scan_limit and self.instance.pk and scan_limit <= self.instance.total_scans:
            raise ValidationError({"scan_limit": "Scan limit must be higher than the current scan count."})

        if is_password_protected and not access_password and not self.instance.access_password_hash:
            raise ValidationError({"access_password": "Set a password for protected QR codes."})

        if self.user:
            limits = self.user.plan_limits
            if cleaned.get("logo") and not limits["logo"]:
                raise ValidationError({"logo": "Logo uploads require a paid plan."})
            if cleaned.get("outer_shape") not in (QRCode.OuterShape.SQUARE, QRCode.OuterShape.ROUNDED) and not limits["custom_shapes"]:
                raise ValidationError({"outer_shape": "Custom QR shapes require a paid plan."})
            if any((cleaned.get(k) or "").strip() for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content")) and not limits["utm"]:
                raise ValidationError("UTM auto-append requires a paid plan.")
            if (cleaned.get("scheduled_active_from") or cleaned.get("scheduled_active_until")) and not limits["scheduled"]:
                raise ValidationError("Scheduled activation requires a paid plan.")
            if cleaned.get("scan_limit") and limits["max_scans"] > 0 and cleaned["scan_limit"] > limits["max_scans"]:
                raise ValidationError({"scan_limit": f"Your current plan allows up to {limits['max_scans']:,} scans per QR."})

        return cleaned

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if logo:
            if logo.size > 2 * 1024 * 1024:  # 2MB limit
                raise ValidationError("Logo must be under 2MB.")
            ext = logo.name.rsplit(".", 1)[-1].lower()
            if ext not in ("png", "jpg", "jpeg", "svg", "webp"):
                raise ValidationError("Logo must be PNG, JPG, SVG, or WebP.")
        return logo

    def save(self, commit=True):
        qr = super().save(commit=False)
        access_password = self.cleaned_data.get("access_password")

        if self.cleaned_data.get("is_password_protected"):
            if access_password:
                qr.set_access_password(access_password)
        else:
            qr.access_password_hash = ""

        # Collect UTM params into JSON field
        utm = {}
        for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content"):
            val = (self.cleaned_data.get(key) or "").strip()
            if val:
                utm[key] = val
        qr.utm_params = utm

        if commit:
            qr.save()
            self.save_m2m()
        return qr
