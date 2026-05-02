"""
Account forms with strict server-side validation.
"""
import re
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.accounts.models import UserProfile

User = get_user_model()


class SignupForm(forms.Form):
    full_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"placeholder": "Full name"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "Email address"}))
    password = forms.CharField(
        min_length=10,
        widget=forms.PasswordInput(attrs={"placeholder": "Password (min 10 chars)"}),
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Confirm password"})
    )
    agree_terms = forms.BooleanField(required=True)

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
    email = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "Email address"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Password"}))
    remember_me = forms.BooleanField(required=False)

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


class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput())
    new_password = forms.CharField(min_length=10, widget=forms.PasswordInput())
    confirm_password = forms.CharField(widget=forms.PasswordInput())

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("new_password") != cleaned.get("confirm_password"):
            raise ValidationError({"confirm_password": "Passwords do not match."})
        return cleaned


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "Your account email"}))


class ResetPasswordForm(forms.Form):
    password = forms.CharField(min_length=10, widget=forms.PasswordInput(attrs={"placeholder": "New password"}))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Confirm new password"}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") != cleaned.get("confirm_password"):
            raise ValidationError({"confirm_password": "Passwords do not match."})
        return cleaned
