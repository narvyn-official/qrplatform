"""
Account forms with strict server-side validation.
"""
import re
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.accounts.models import BusinessVerification, UserProfile
from apps.accounts.verification import normalize_business_domain

User = get_user_model()


INPUT_CLASS = "input"
CHECKBOX_CLASS = "rounded border-surface-300 text-primary-600 focus:ring-primary-500"


class SignupForm(forms.Form):
    full_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={
        "placeholder": "Full name", "class": INPUT_CLASS, "autocomplete": "name",
    }))
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        "placeholder": "Email address", "class": INPUT_CLASS, "autocomplete": "email",
    }))
    password = forms.CharField(
        min_length=10,
        widget=forms.PasswordInput(attrs={
            "placeholder": "Password (min 10 chars)", "class": INPUT_CLASS, "autocomplete": "new-password",
        }),
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "placeholder": "Confirm password", "class": INPUT_CLASS, "autocomplete": "new-password",
        })
    )
    agree_terms = forms.BooleanField(required=True, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        if User.objects.filter(email=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        pw1 = cleaned.get("password")
        pw2 = cleaned.get("confirm_password")
        if pw1 and pw2 and pw1 != pw2:
            raise ValidationError({"confirm_password": "Passwords do not match."})
        return cleaned

    def save(self):
        data = self.cleaned_data
        user = User.objects.create_user(
            email=data["email"],
            password=data["password"],
            full_name=data["full_name"],
        )
        return user


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        "placeholder": "you@company.com", "class": INPUT_CLASS, "autocomplete": "email",
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        "placeholder": "Password", "class": INPUT_CLASS, "autocomplete": "current-password",
    }))
    remember_me = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))

    def clean_email(self):
        return self.cleaned_data["email"].lower().strip()


class ResendVerificationForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        "placeholder": "you@company.com", "class": INPUT_CLASS, "autocomplete": "email",
    }))

    def clean_email(self):
        return self.cleaned_data["email"].lower().strip()


class ProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = [
            "avatar", "company", "website", "timezone", "phone", "bio",
            "brand_name", "brand_color",
            "email_weekly_report", "email_scan_alerts", "scan_alert_threshold",
        ]
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 3}),
            "brand_color": forms.TextInput(attrs={"type": "color"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", CHECKBOX_CLASS)
            elif isinstance(field.widget, forms.FileInput):
                field.widget.attrs.setdefault("class", "block w-full text-sm text-gray-600 file:mr-4 file:rounded-lg file:border-0 file:bg-primary-50 file:px-3 file:py-2 file:text-sm file:font-semibold file:text-primary-700 hover:file:bg-primary-100")
            elif name == "brand_color":
                field.widget.attrs.setdefault("class", "h-11 w-full rounded-lg border border-surface-200 bg-white p-1")
            else:
                field.widget.attrs.setdefault("class", INPUT_CLASS)

    def clean_website(self):
        url = self.cleaned_data.get("website", "")
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "")
        if phone and not re.match(r"^\+?[\d\s\-().]{7,20}$", phone):
            raise ValidationError("Enter a valid phone number.")
        return phone


class BusinessVerificationForm(forms.ModelForm):
    class Meta:
        model = BusinessVerification
        fields = ["business_name", "domain", "method"]
        widgets = {
            "business_name": forms.TextInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "Acme Foods Pvt Ltd",
                "autocomplete": "organization",
            }),
            "domain": forms.TextInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "example.com",
                "autocomplete": "url",
            }),
            "method": forms.Select(attrs={"class": INPUT_CLASS}),
        }

    def clean_business_name(self):
        name = self.cleaned_data["business_name"].strip()
        if len(name) < 2:
            raise ValidationError("Business name is required.")
        return name

    def clean_domain(self):
        return normalize_business_domain(self.cleaned_data.get("domain"))


class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput(attrs={
        "class": INPUT_CLASS, "autocomplete": "current-password", "placeholder": "Current password",
    }))
    new_password = forms.CharField(min_length=10, widget=forms.PasswordInput(attrs={
        "class": INPUT_CLASS, "autocomplete": "new-password", "placeholder": "New password",
    }))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={
        "class": INPUT_CLASS, "autocomplete": "new-password", "placeholder": "Confirm new password",
    }))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("new_password") != cleaned.get("confirm_password"):
            raise ValidationError({"confirm_password": "Passwords do not match."})
        return cleaned


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        "placeholder": "Your account email", "class": INPUT_CLASS, "autocomplete": "email",
    }))


class ResetPasswordForm(forms.Form):
    password = forms.CharField(min_length=10, widget=forms.PasswordInput(attrs={
        "placeholder": "New password", "class": INPUT_CLASS, "autocomplete": "new-password",
    }))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={
        "placeholder": "Confirm new password", "class": INPUT_CLASS, "autocomplete": "new-password",
    }))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") != cleaned.get("confirm_password"):
            raise ValidationError({"confirm_password": "Passwords do not match."})
        return cleaned
