"""
QR Code forms.
"""
from django import forms
from django.core.exceptions import ValidationError
from apps.qrcodes.models import QRCode, QRCodeCampaign
from apps.qrcodes.utils import (
    build_email_content,
    build_sms_content,
    build_whatsapp_content,
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

    class Meta:
        model = QRCode
        fields = [
            "name", "qr_type", "content", "destination_url",
            "campaign", "tags",
            "foreground_color", "background_color",
            "dot_style", "corner_style",
            "logo", "logo_size_ratio",
            "frame_text", "frame_color",
            "error_correction", "qr_size",
            "expires_at", "scan_limit",
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
            if not destination_url.startswith(("http://", "https://")):
                destination_url = "https://" + destination_url
                cleaned["destination_url"] = destination_url
            cleaned["content"] = destination_url

        elif qr_type == QRCode.QRType.URL:
            if not content:
                raise ValidationError({"content": "Website URL is required."})
            if not content.startswith(("http://", "https://")):
                content = "https://" + content
            cleaned["content"] = content

        elif qr_type == QRCode.QRType.WHATSAPP:
            if not content:
                raise ValidationError({"content": "WhatsApp phone number is required."})
            if not content.startswith(("http://", "https://")):
                cleaned["content"] = build_whatsapp_content(content)

        elif qr_type == QRCode.QRType.EMAIL:
            if not content:
                raise ValidationError({"content": "Email content is required."})
            if not content.startswith("mailto:") and "@" in content and "\n" not in content:
                cleaned["content"] = build_email_content(content)

        elif qr_type == QRCode.QRType.SMS:
            if not content:
                raise ValidationError({"content": "SMS content is required."})
            if not content.startswith(("SMSTO:", "sms:")):
                parts = content.split("|", 1)
                phone = parts[0].strip()
                message = parts[1].strip() if len(parts) > 1 else ""
                cleaned["content"] = build_sms_content(phone, message)

        if not content and qr_type != QRCode.QRType.DYNAMIC:
            raise ValidationError({"content": "Content is required."})

        if is_password_protected and not access_password and not self.instance.access_password_hash:
            raise ValidationError({"access_password": "Set a password for protected QR codes."})

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

        if commit:
            qr.save()
            self.save_m2m()
        return qr
