from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import BusinessVerification
from apps.accounts.plans import PLAN_CATALOG
from apps.qrcodes.models import Certificate, QRCode, QRSuspiciousReport

User = get_user_model()


class AdminUserUpdateForm(forms.ModelForm):
    plan = forms.ChoiceField(choices=[(code, plan.name) for code, plan in PLAN_CATALOG.items()])
    plan_expires_at = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    class Meta:
        model = User
        fields = ["full_name", "role", "plan", "plan_expires_at", "is_active", "is_email_verified", "is_staff"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({"class": "h-4 w-4 rounded border-surface-300 text-primary-600 focus:ring-primary-600"})
            else:
                field.widget.attrs.update({"class": "input"})

    def clean_plan_expires_at(self):
        value = self.cleaned_data.get("plan_expires_at")
        if value and timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("plan") == "free":
            cleaned["plan_expires_at"] = None
        return cleaned


class AdminQRCodeActionForm(forms.Form):
    action = forms.ChoiceField(choices=[
        ("active", "Activate"),
        ("paused", "Pause"),
        ("deleted", "Mark deleted"),
    ])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["action"].widget.attrs.update({"class": "input"})

    def save(self, qr):
        qr.status = self.cleaned_data["action"]
        qr.save(update_fields=["status", "updated_at"])
        return qr


class AdminCertificateActionForm(forms.Form):
    status = forms.ChoiceField(choices=Certificate.Status.choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].widget.attrs.update({"class": "input"})

    def save(self, certificate):
        status = self.cleaned_data["status"]
        certificate.status = status
        if status == Certificate.Status.REVOKED:
            certificate.revoked_at = timezone.now()
            if certificate.qrcode_id:
                certificate.qrcode.status = QRCode.Status.PAUSED
                certificate.qrcode.save(update_fields=["status", "updated_at"])
        elif status == Certificate.Status.VALID:
            certificate.revoked_at = None
            if certificate.qrcode_id and certificate.qrcode.status == QRCode.Status.PAUSED:
                certificate.qrcode.status = QRCode.Status.ACTIVE
                certificate.qrcode.save(update_fields=["status", "updated_at"])
        certificate.save(update_fields=["status", "revoked_at", "updated_at"])
        return certificate


class AdminVerificationActionForm(forms.Form):
    status = forms.ChoiceField(choices=BusinessVerification.Status.choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].widget.attrs.update({"class": "input"})

    def save(self, verification):
        status = self.cleaned_data["status"]
        verification.status = status
        if status == BusinessVerification.Status.VERIFIED:
            verification.verified_at = timezone.now()
            verification.revoked_at = None
        elif status == BusinessVerification.Status.REVOKED:
            verification.revoked_at = timezone.now()
        verification.save(update_fields=["status", "verified_at", "revoked_at", "updated_at"])
        return verification


class AdminReportUpdateForm(forms.ModelForm):
    class Meta:
        model = QRSuspiciousReport
        fields = ["status", "admin_notes"]
        widgets = {
            "status": forms.Select(attrs={"class": "input"}),
            "admin_notes": forms.Textarea(attrs={"class": "input", "rows": 5}),
        }

    def clean_admin_notes(self):
        return (self.cleaned_data.get("admin_notes") or "").strip()[:5000]
