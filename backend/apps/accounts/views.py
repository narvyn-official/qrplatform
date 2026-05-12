"""
Account views — signup, login, email verification, password reset, profile.
"""
import logging
import secrets
from urllib.parse import urlencode

import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import (
    login, logout, authenticate, get_user_model, update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.conf import settings
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.models import (
    EmailVerificationToken, PasswordResetToken, UserProfile, AuditLog, APIKey
)
from apps.accounts.forms import (
    SignupForm, LoginForm, ProfileForm, ChangePasswordForm,
    ForgotPasswordForm, ResetPasswordForm, ResendVerificationForm,
)
from apps.analytics.utils import get_client_ip

User = get_user_model()
logger = logging.getLogger(__name__)
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


def axes_lockout_handler(request, credentials, *args, **kwargs):
    """Render the login page with a lockout error instead of a bare 403 text response."""
    from apps.accounts.forms import LoginForm
    form = LoginForm()
    messages.error(request, "Account locked due to too many failed attempts. Please try again in 30 minutes.")
    return render(request, "accounts/login.html", {"form": form}, status=403)


def _log_audit(user, action, request, **metadata):
    AuditLog.objects.create(
        user=user,
        action=action,
        ip_address=get_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        metadata=metadata,
    )


def _safe_next_url(request):
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return settings.LOGIN_REDIRECT_URL


def _email_verification_required():
    return bool(getattr(settings, "EMAIL_VERIFICATION_REQUIRED", False))


def _google_oauth_configured():
    return bool(
        getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
        and getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "")
    )


def _google_redirect_uri(request):
    return request.build_absolute_uri("/accounts/google/callback/")


def _auth_context(request, extra=None):
    context = {
        "google_oauth_configured": _google_oauth_configured(),
    }
    if extra:
        context.update(extra)
    return context


def _login_context(request, form, verification_email=None):
    pending_email = ""
    if _email_verification_required():
        pending_email = verification_email or request.session.get("pending_verification_email", "")
    next_url = request.GET.get("next", "")
    if next_url and not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = ""
    return _auth_context(request, {
        "form": form,
        "resend_form": ResendVerificationForm(initial={"email": pending_email}),
        "verification_email": pending_email,
        "next_url": next_url,
    })


@never_cache
def signup(request):
    if request.user.is_authenticated:
        return redirect("qrcodes:dashboard")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            if _email_verification_required():
                _send_verification_email(user, request)
                request.session["pending_verification_email"] = user.email
                messages.info(request, "Account created! Check your email to verify.")
            else:
                user.is_active = True
                user.is_email_verified = True
                user.save(update_fields=["is_active", "is_email_verified"])
                UserProfile.objects.get_or_create(user=user)
                request.session.pop("pending_verification_email", None)
                messages.success(request, "Account created. You can sign in now.")
            return redirect("accounts:login")
    else:
        form = SignupForm()

    return render(request, "accounts/signup.html", _auth_context(request, {"form": form}))


@never_cache
def google_login(request):
    if request.user.is_authenticated:
        return redirect("qrcodes:dashboard")
    if not _google_oauth_configured():
        messages.error(request, "Google sign-in is not configured yet.")
        return redirect("accounts:login")

    state = secrets.token_urlsafe(32)
    request.session["google_oauth_state"] = state
    request.session["google_oauth_next"] = _safe_next_url(request)

    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": _google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@never_cache
def google_callback(request):
    if not _google_oauth_configured():
        messages.error(request, "Google sign-in is not configured yet.")
        return redirect("accounts:login")

    error = request.GET.get("error")
    if error:
        messages.error(request, "Google sign-in was cancelled or denied.")
        return redirect("accounts:login")

    state = request.GET.get("state", "")
    expected_state = request.session.pop("google_oauth_state", "")
    if not state or state != expected_state:
        messages.error(request, "Google sign-in could not be verified. Please try again.")
        return redirect("accounts:login")

    code = request.GET.get("code", "")
    if not code:
        messages.error(request, "Google did not return a sign-in code.")
        return redirect("accounts:login")

    try:
        token_response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": _google_redirect_uri(request),
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        token_response.raise_for_status()
        tokens = token_response.json()
        id_token = tokens.get("id_token")
        if not id_token:
            raise ValueError("Missing Google ID token.")

        profile_response = requests.get(
            GOOGLE_TOKENINFO_URL,
            params={"id_token": id_token},
            timeout=10,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
    except Exception as exc:
        logger.exception("Google sign-in failed: %s", exc)
        messages.error(request, "Google sign-in failed. Please try email login or try again.")
        return redirect("accounts:login")

    if profile.get("aud") != settings.GOOGLE_OAUTH_CLIENT_ID:
        messages.error(request, "Google sign-in response did not match this app.")
        return redirect("accounts:login")
    if profile.get("email_verified") not in (True, "true", "True", "1", 1):
        messages.error(request, "Google did not confirm this email address.")
        return redirect("accounts:login")

    google_sub = profile.get("sub", "")
    email = (profile.get("email") or "").lower().strip()
    full_name = (profile.get("name") or "").strip()
    if not google_sub or not email:
        messages.error(request, "Google did not return enough account details.")
        return redirect("accounts:login")

    user = User.objects.filter(google_sub=google_sub).first()
    if not user:
        user = User.objects.filter(email=email).first()
        if user:
            user.google_sub = google_sub
            user.is_active = True
            user.is_email_verified = True
            if full_name and not user.full_name:
                user.full_name = full_name
            user.save(update_fields=["google_sub", "is_active", "is_email_verified", "full_name"])
        else:
            user = User.objects.create_user(
                email=email,
                password=None,
                full_name=full_name,
                google_sub=google_sub,
                is_active=True,
                is_email_verified=True,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            UserProfile.objects.get_or_create(user=user)
    else:
        update_fields = []
        if not user.is_active:
            user.is_active = True
            update_fields.append("is_active")
        if not user.is_email_verified:
            user.is_email_verified = True
            update_fields.append("is_email_verified")
        if full_name and not user.full_name:
            user.full_name = full_name
            update_fields.append("full_name")
        if update_fields:
            user.save(update_fields=update_fields)

    UserProfile.objects.get_or_create(user=user)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    user.update_last_activity()
    _log_audit(user, AuditLog.Action.LOGIN, request, provider="google")
    request.session.pop("pending_verification_email", None)
    next_url = request.session.pop("google_oauth_next", settings.LOGIN_REDIRECT_URL)
    return redirect(next_url)


def _send_verification_email(user, request):
    if not _email_verification_required():
        user.is_active = True
        user.is_email_verified = True
        user.save(update_fields=["is_active", "is_email_verified"])
        UserProfile.objects.get_or_create(user=user)
        EmailVerificationToken.objects.filter(user=user).delete()
        return

    token_obj, _ = EmailVerificationToken.objects.update_or_create(
        user=user,
        defaults={
            "token": "",
            "expires_at": timezone.now() + timezone.timedelta(hours=24),
        },
    )
    # Force token regeneration
    token_obj.token = secrets.token_urlsafe(48)
    token_obj.save()

    verify_url = request.build_absolute_uri(f"/accounts/verify/{token_obj.token}/")
    context = {"user": user, "verify_url": verify_url, "platform_name": settings.PLATFORM_NAME}

    html_body = render_to_string("emails/verify_email.html", context)
    text_body = render_to_string("emails/verify_email.txt", context)

    msg = EmailMultiAlternatives(
        subject=f"Verify your {settings.PLATFORM_NAME} account",
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    msg.attach_alternative(html_body, "text/html")
    try:
        msg.send()
    except Exception as exc:
        logger.error("Failed to send verification email to %s: %s", user.email, exc)


def verify_email(request, token):
    token_obj = get_object_or_404(EmailVerificationToken, token=token)

    if token_obj.is_expired:
        messages.error(request, "Verification link has expired. Please request a new one.")
        return redirect("accounts:login")

    user = token_obj.user
    user.is_active = True
    user.is_email_verified = True
    user.save(update_fields=["is_active", "is_email_verified"])
    token_obj.delete()

    # Create profile if it doesn't exist
    UserProfile.objects.get_or_create(user=user)

    _log_audit(user, AuditLog.Action.EMAIL_VERIFY, request)
    messages.success(request, "Email verified! You can now log in.")
    return redirect("accounts:login")


@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect("qrcodes:dashboard")

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            password = form.cleaned_data["password"]
            user = authenticate(request, username=email, password=password)
            verification_email = None

            if user is not None:
                if _email_verification_required() and not user.is_email_verified:
                    verification_email = user.email
                    request.session["pending_verification_email"] = user.email
                    _log_audit(user, AuditLog.Action.LOGIN_FAILED, request, email=email, reason="email_unverified")
                    messages.warning(request, "Please verify your email before logging in.")
                else:
                    if form.cleaned_data.get("remember_me"):
                        request.session.set_expiry(60 * 60 * 24 * 7)
                    else:
                        request.session.set_expiry(0)
                    login(request, user)
                    user.update_last_activity()
                    _log_audit(user, AuditLog.Action.LOGIN, request)
                    request.session.pop("pending_verification_email", None)
                    return redirect(_safe_next_url(request))
            else:
                _log_audit(None, AuditLog.Action.LOGIN_FAILED, request, email=email)
                try:
                    unverified = User.objects.get(email=email)
                    if (
                        not _email_verification_required()
                        and unverified.check_password(password)
                    ):
                        unverified.is_active = True
                        unverified.is_email_verified = True
                        unverified.save(update_fields=["is_active", "is_email_verified"])
                        if form.cleaned_data.get("remember_me"):
                            request.session.set_expiry(60 * 60 * 24 * 7)
                        else:
                            request.session.set_expiry(0)
                        login(request, unverified, backend=settings.AUTHENTICATION_BACKENDS[0])
                        unverified.update_last_activity()
                        _log_audit(unverified, AuditLog.Action.LOGIN, request)
                        request.session.pop("pending_verification_email", None)
                        return redirect(_safe_next_url(request))
                    if (
                        _email_verification_required()
                        and not unverified.is_email_verified
                        and unverified.check_password(password)
                    ):
                        verification_email = unverified.email
                        request.session["pending_verification_email"] = unverified.email
                        messages.warning(request, "Please verify your email before logging in.")
                    else:
                        messages.error(request, "Invalid email or password.")
                except User.DoesNotExist:
                    messages.error(request, "Invalid email or password.")
            if verification_email:
                return render(request, "accounts/login.html", _login_context(request, form, verification_email))
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", _login_context(request, form))


@never_cache
@require_POST
def resend_verification(request):
    if not _email_verification_required():
        request.session.pop("pending_verification_email", None)
        messages.info(request, "Email verification is currently disabled. You can log in directly.")
        return redirect("accounts:login")

    form = ResendVerificationForm(request.POST)
    if form.is_valid():
        email = form.cleaned_data["email"]
        try:
            user = User.objects.get(email=email)
            if not user.is_email_verified:
                _send_verification_email(user, request)
                request.session["pending_verification_email"] = user.email
        except User.DoesNotExist:
            pass
    messages.info(request, "If this account still needs verification, a new email has been sent.")
    return redirect("accounts:login")


@login_required
@require_POST
def logout_view(request):
    _log_audit(request.user, AuditLog.Action.LOGOUT, request)
    logout(request)
    return redirect("accounts:login")


def forgot_password(request):
    if request.method == "POST":
        form = ForgotPasswordForm(request.POST)
        if form.is_valid():
            if not _password_reset_email_delivery_ready():
                messages.error(request, "Password reset email delivery is not configured yet. Please contact support.")
                return redirect("accounts:forgot_password")

            email = form.cleaned_data["email"]
            try:
                user = User.objects.get(email=email, is_active=True)
                if not _send_password_reset_email(user, request):
                    messages.error(request, "Password reset email could not be sent right now. Please try again shortly.")
                    return redirect("accounts:forgot_password")
            except User.DoesNotExist:
                pass  # Don't reveal existence
            messages.info(request, "If that email exists, a reset link has been sent.")
            return redirect("accounts:forgot_password")
    else:
        form = ForgotPasswordForm()

    return render(request, "accounts/forgot_password.html", {"form": form})


def _password_reset_email_delivery_ready():
    backend = getattr(settings, "EMAIL_BACKEND", "")
    non_delivery_backends = (
        "django.core.mail.backends.console.EmailBackend",
        "django.core.mail.backends.dummy.EmailBackend",
        "django.core.mail.backends.filebased.EmailBackend",
    )
    if backend in non_delivery_backends:
        return False
    if backend == "django.core.mail.backends.smtp.EmailBackend":
        return all([
            getattr(settings, "EMAIL_HOST", ""),
            getattr(settings, "EMAIL_HOST_USER", ""),
            getattr(settings, "EMAIL_HOST_PASSWORD", ""),
            getattr(settings, "DEFAULT_FROM_EMAIL", ""),
        ])
    return bool(getattr(settings, "DEFAULT_FROM_EMAIL", ""))


def _send_password_reset_email(user, request):
    # Invalidate old tokens
    PasswordResetToken.objects.filter(user=user, is_used=False).update(is_used=True)

    token_obj = PasswordResetToken.objects.create(user=user)
    reset_url = request.build_absolute_uri(f"/accounts/reset-password/{token_obj.token}/")

    context = {"user": user, "reset_url": reset_url, "platform_name": settings.PLATFORM_NAME}
    html_body = render_to_string("emails/reset_password.html", context)
    text_body = render_to_string("emails/reset_password.txt", context)

    msg = EmailMultiAlternatives(
        subject=f"Reset your {settings.PLATFORM_NAME} password",
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    msg.attach_alternative(html_body, "text/html")
    try:
        msg.send(fail_silently=False)
    except Exception as exc:
        logger.error("Failed to send reset email to %s: %s", user.email, exc)
        return False
    return True


def reset_password(request, token):
    token_obj = get_object_or_404(PasswordResetToken, token=token)

    if not token_obj.is_valid:
        messages.error(request, "This reset link is invalid or has expired.")
        return redirect("accounts:forgot_password")

    if request.method == "POST":
        form = ResetPasswordForm(request.POST)
        if form.is_valid():
            token_obj.user.set_password(form.cleaned_data["password"])
            token_obj.user.save()
            token_obj.is_used = True
            token_obj.save()
            _log_audit(token_obj.user, AuditLog.Action.PASSWORD_RESET, request)
            messages.success(request, "Password updated! Please log in.")
            return redirect("accounts:login")
    else:
        form = ResetPasswordForm()

    return render(request, "accounts/reset_password.html", {"form": form, "token": token})


@login_required
def profile(request):
    profile_obj, _ = UserProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(instance=profile_obj)

    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=profile_obj)
        if form.is_valid():
            if "full_name" in request.POST:
                request.user.full_name = request.POST.get("full_name", "").strip()[:150]
                request.user.save(update_fields=["full_name"])
            form.save()
            _log_audit(request.user, AuditLog.Action.PROFILE_UPDATE, request)
            messages.success(request, "Profile updated.")
            return redirect("accounts:profile")

    api_keys = APIKey.objects.filter(user=request.user, status=APIKey.Status.ACTIVE)
    # Pop the raw key from session so it shows only once
    new_api_key = request.session.pop("new_api_key", None)
    context = {
        "form": form,
        "password_form": ChangePasswordForm(),
        "profile": profile_obj,
        "api_keys": api_keys,
        "new_api_key": new_api_key,
        "active_tab": "profile",
        "email_verification_required": _email_verification_required(),
    }
    return render(request, "accounts/profile.html", context)


@login_required
@require_POST
def change_password(request):
    form = ChangePasswordForm(request.POST)
    if form.is_valid():
        if not request.user.check_password(form.cleaned_data["current_password"]):
            messages.error(request, "Current password is incorrect.")
        else:
            request.user.set_password(form.cleaned_data["new_password"])
            request.user.save(update_fields=["password"])
            update_session_auth_hash(request, request.user)
            _log_audit(request.user, AuditLog.Action.PASSWORD_CHANGE, request)
            messages.success(request, "Password changed successfully.")
    else:
        first_error = next(iter(form.errors.values()))[0]
        messages.error(request, first_error)
    return redirect("accounts:profile")


@login_required
@require_POST
def create_api_key(request):
    if not request.user.plan_limits["api"]:
        messages.error(request, "API keys require a paid membership.")
        return redirect("accounts:profile")

    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, "API key name is required.")
        return redirect("accounts:profile")

    existing_count = APIKey.objects.filter(user=request.user, status=APIKey.Status.ACTIVE).count()
    if existing_count >= 10:
        messages.error(request, "Maximum 10 active API keys allowed.")
        return redirect("accounts:profile")

    key_obj, raw_key = APIKey.generate(request.user, name)
    _log_audit(request.user, AuditLog.Action.API_KEY_CREATE, request, key_name=name)

    # Show raw key once via session (never stored in DB)
    request.session["new_api_key"] = raw_key
    messages.success(request, "API key created. Copy it now — it won't be shown again.")
    return redirect("accounts:profile")


@login_required
@require_POST
def revoke_api_key(request, key_id):
    key_obj = get_object_or_404(APIKey, id=key_id, user=request.user)
    key_obj.status = APIKey.Status.REVOKED
    key_obj.save(update_fields=["status"])
    _log_audit(request.user, AuditLog.Action.API_KEY_REVOKE, request, key_name=key_obj.name)
    messages.success(request, f'API key "{key_obj.name}" revoked.')
    return redirect("accounts:profile")
