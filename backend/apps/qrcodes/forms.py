"""QR Code and certificate forms."""

import csv
import io

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.qrcodes.models import QRCode, QRCodeCampaign, Certificate
from apps.qrcodes.utils import (
    build_email_content,
    build_sms_content,
    build_whatsapp_content,
)
from apps.qrcodes.validation import (
    EMAIL_VALIDATOR,
    MAX_LOGO_SIZE_RATIO,
    MAX_QR_SIZE,
    MIN_QR_SIZE,
    normalize_http_url,
    validate_hex_color,
    validate_phone_number,
)


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
        self.fields["outer_shape"].widget = forms.HiddenInput()
        self.fields["outer_shape"].required = False
        self.fields["outer_shape"].initial = self.instance.outer_shape or QRCode.OuterShape.SQUARE
        self.fields["dot_style"].choices = [
            (QRCode.DotStyle.SQUARE, "Classic"),
            (QRCode.DotStyle.ROUNDED, "Soft rounded"),
            (QRCode.DotStyle.DOTS, "Dots"),
            (QRCode.DotStyle.CLASSY, "Clean grid"),
        ]
        self.fields["corner_style"].choices = [
            (QRCode.CornerStyle.SQUARE, "Square"),
            (QRCode.CornerStyle.EXTRA_ROUNDED, "Rounded"),
            (QRCode.CornerStyle.DOT, "Dot"),
        ]

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
        cleaned["outer_shape"] = cleaned.get("outer_shape") or QRCode.OuterShape.SQUARE
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
            validate_phone_number(content, "content", "Enter a valid WhatsApp phone number with country code.")
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
                validate_phone_number(phone, "content", "Enter a valid SMS phone number.")
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


class CertificateForm(forms.ModelForm):
    """Guided certificate creation form with server-side validation."""

    class Meta:
        model = Certificate
        fields = [
            "recipient_name",
            "title",
            "issuer",
            "issue_date",
            "expiry_date",
            "certificate_id",
            "pdf_url",
        ]
        widgets = {
            "recipient_name": forms.TextInput(attrs={"placeholder": "Aarav Sharma"}),
            "title": forms.TextInput(attrs={"placeholder": "Certificate of Completion"}),
            "issuer": forms.TextInput(attrs={"placeholder": "Narvyn Academy"}),
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "expiry_date": forms.DateInput(attrs={"type": "date"}),
            "certificate_id": forms.TextInput(attrs={"placeholder": "CERT-2026-001"}),
        }

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.workspace = workspace
        input_class = (
            "block w-full px-3.5 py-2.5 border border-surface-200 rounded-lg text-sm "
            "focus:ring-2 focus:ring-primary-600 focus:border-transparent outline-none transition bg-white shadow-[0_1px_0_rgba(15,23,42,.03)]"
        )
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", input_class)
        self.fields["expiry_date"].required = False
        self.fields["pdf_url"].required = False
        self.fields["pdf_url"].widget.attrs.setdefault("accept", "application/pdf,.pdf")

    def clean_certificate_id(self):
        certificate_id = (self.cleaned_data.get("certificate_id") or "").strip()
        if not certificate_id:
            raise ValidationError("Certificate ID is required.")
        if self.workspace:
            qs = Certificate.objects.filter(workspace=self.workspace, certificate_id__iexact=certificate_id)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError("This certificate ID already exists in your workspace.")
        return certificate_id

    def clean_pdf_url(self):
        uploaded = self.cleaned_data.get("pdf_url")
        if not uploaded:
            return uploaded
        name = uploaded.name.lower()
        content_type = getattr(uploaded, "content_type", "")
        if not name.endswith(".pdf") or content_type not in ("application/pdf", "application/octet-stream", ""):
            raise ValidationError("Upload a PDF file.")
        if uploaded.size > 10 * 1024 * 1024:
            raise ValidationError("PDF file must be 10 MB or smaller.")
        return uploaded

    def clean(self):
        cleaned = super().clean()
        issue_date = cleaned.get("issue_date")
        expiry_date = cleaned.get("expiry_date")
        if issue_date and issue_date > timezone.localdate():
            raise ValidationError({"issue_date": "Issue date cannot be in the future."})
        if issue_date and expiry_date and expiry_date < issue_date:
            raise ValidationError({"expiry_date": "Expiry date must be after the issue date."})
        return cleaned


class CertificateBulkUploadForm(forms.Form):
    """CSV upload for creating many certificate verification QR records."""

    csv_file = forms.FileField(
        label="CSV file",
        widget=forms.FileInput(attrs={"accept": ".csv,text/csv", "class": "input"}),
        help_text="Required columns: recipient_name, title, issuer, issue_date, certificate_id. Optional: expiry_date.",
    )

    required_columns = {"recipient_name", "title", "issuer", "issue_date", "certificate_id"}
    optional_columns = {"expiry_date"}

    def clean_csv_file(self):
        uploaded = self.cleaned_data["csv_file"]
        if not uploaded.name.lower().endswith(".csv"):
            raise ValidationError("Upload a CSV file.")
        if uploaded.size > 2 * 1024 * 1024:
            raise ValidationError("CSV file must be 2 MB or smaller.")
        return uploaded

    def parsed_rows(self):
        uploaded = self.cleaned_data["csv_file"]
        uploaded.seek(0)
        try:
            text = uploaded.read().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("CSV must be UTF-8 encoded.") from exc

        reader = csv.DictReader(io.StringIO(text))
        headers = {header.strip() for header in (reader.fieldnames or []) if header}
        missing = self.required_columns - headers
        if missing:
            raise ValidationError(f"Missing required column(s): {', '.join(sorted(missing))}.")

        rows = []
        for index, row in enumerate(reader, start=2):
            normalized = {
                key.strip(): (value or "").strip()
                for key, value in row.items()
                if key
            }
            if not any(normalized.values()):
                continue
            normalized["_row_number"] = index
            rows.append(normalized)
        if not rows:
            raise ValidationError("CSV does not contain certificate rows.")
        if len(rows) > 500:
            raise ValidationError("Upload 500 certificates or fewer at a time.")
        return rows
