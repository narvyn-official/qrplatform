"""
Comprehensive tests for the accounts app.
Covers: models, forms, and views.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core import mail

from apps.accounts.models import (
    UserProfile, EmailVerificationToken, PasswordResetToken, APIKey, AuditLog,
    MembershipOrder, BusinessVerification, BusinessVerificationAttempt,
)
from apps.accounts.forms import (
    SignupForm, LoginForm, ProfileForm, ChangePasswordForm,
    ForgotPasswordForm, ResetPasswordForm, ResendVerificationForm,
    BusinessVerificationForm,
)

User = get_user_model()

CACHE_OVERRIDE = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}


def make_active_user(email="test@example.com", password="testpass123", **kw):
    """Create a fully-active, verified user."""
    user = User.objects.create_user(
        email=email,
        password=password,
        full_name=kw.pop("full_name", "Test User"),
        **kw,
    )
    user.is_active = True
    user.is_email_verified = True
    user.save(update_fields=["is_active", "is_email_verified"])
    UserProfile.objects.get_or_create(user=user)
    return user


# ── Model Tests ───────────────────────────────────────────────────────────────

class UserManagerTests(TestCase):

    def test_create_user_requires_email(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="pass123456")

    def test_create_user_normalises_email(self):
        user = User.objects.create_user(email="TEST@Example.COM", password="pass123456")
        self.assertEqual(user.email, "test@example.com")

    def test_create_user_inactive_by_default(self):
        user = User.objects.create_user(email="u@example.com", password="pass123456")
        self.assertFalse(user.is_active)

    def test_create_superuser_flags(self):
        su = User.objects.create_superuser(email="admin@example.com", password="adminpass123")
        self.assertTrue(su.is_staff)
        self.assertTrue(su.is_superuser)
        self.assertTrue(su.is_active)
        self.assertEqual(su.role, "admin")


class UserModelTests(TestCase):

    def setUp(self):
        self.user = make_active_user()

    def test_display_name_uses_full_name(self):
        self.user.full_name = "Alice Smith"
        self.assertEqual(self.user.display_name, "Alice Smith")

    def test_display_name_falls_back_to_email_prefix(self):
        self.user.full_name = ""
        self.assertEqual(self.user.display_name, "test")

    def test_is_pro_for_pro_role(self):
        self.user.role = User.Role.PRO
        self.assertTrue(self.user.is_pro)

    def test_is_pro_for_enterprise_role(self):
        self.user.role = User.Role.ENTERPRISE
        self.assertTrue(self.user.is_pro)

    def test_is_pro_false_for_user_role(self):
        self.user.role = User.Role.USER
        self.assertFalse(self.user.is_pro)

    def test_plan_expiry_falls_back_to_free(self):
        self.user.role = User.Role.PRO
        self.user.plan = "pro"
        self.user.plan_expires_at = timezone.now() - timedelta(days=1)
        self.assertEqual(self.user.active_plan_code, "free")

    def test_update_last_activity(self):
        self.user.update_last_activity()
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_activity)

    def test_google_sub_can_be_saved(self):
        self.user.google_sub = "google-123"
        self.user.save(update_fields=["google_sub"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.google_sub, "google-123")

    def test_str(self):
        self.assertEqual(str(self.user), self.user.email)


class EmailVerificationTokenTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(email="tok@example.com", password="pass123456")

    def test_token_generated_on_save(self):
        tok = EmailVerificationToken(
            user=self.user,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        tok.save()
        self.assertTrue(len(tok.token) > 20)

    def test_is_expired_false_for_future_expiry(self):
        tok = EmailVerificationToken.objects.create(
            user=self.user,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.assertFalse(tok.is_expired)

    def test_is_expired_true_for_past_expiry(self):
        tok = EmailVerificationToken.objects.create(
            user=self.user,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        self.assertTrue(tok.is_expired)


class PasswordResetTokenTests(TestCase):

    def setUp(self):
        self.user = make_active_user(email="reset@example.com")

    def test_token_generated_on_save(self):
        tok = PasswordResetToken(user=self.user, expires_at=timezone.now() + timedelta(hours=2))
        tok.save()
        self.assertTrue(len(tok.token) > 20)

    def test_is_valid_unused_unexpired(self):
        tok = PasswordResetToken.objects.create(
            user=self.user,
            expires_at=timezone.now() + timedelta(hours=2),
        )
        self.assertTrue(tok.is_valid)

    def test_is_valid_false_when_used(self):
        tok = PasswordResetToken.objects.create(
            user=self.user,
            expires_at=timezone.now() + timedelta(hours=2),
            is_used=True,
        )
        self.assertFalse(tok.is_valid)

    def test_is_valid_false_when_expired(self):
        tok = PasswordResetToken.objects.create(
            user=self.user,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertFalse(tok.is_valid)


class APIKeyTests(TestCase):

    def setUp(self):
        self.user = make_active_user(email="api@example.com")

    def test_generate_returns_key_and_raw(self):
        key_obj, raw_key = APIKey.generate(self.user, "My Key")
        self.assertTrue(raw_key.startswith("qrf_"))
        self.assertEqual(key_obj.user, self.user)
        self.assertEqual(key_obj.name, "My Key")

    def test_key_prefix_set_correctly(self):
        key_obj, raw_key = APIKey.generate(self.user, "Test")
        self.assertEqual(key_obj.key_prefix, raw_key[:8])

    def test_is_active_true_for_active_status(self):
        key_obj, _ = APIKey.generate(self.user, "Active Key")
        self.assertTrue(key_obj.is_active)

    def test_is_active_false_when_revoked(self):
        key_obj, _ = APIKey.generate(self.user, "Revoked Key")
        key_obj.status = APIKey.Status.REVOKED
        self.assertFalse(key_obj.is_active)

    def test_is_active_false_when_expired(self):
        key_obj, _ = APIKey.generate(self.user, "Expired Key")
        key_obj.expires_at = timezone.now() - timedelta(minutes=1)
        self.assertFalse(key_obj.is_active)

    def test_default_scopes(self):
        key_obj, _ = APIKey.generate(self.user, "Default Scope")
        self.assertIn("qr:read", key_obj.scopes)
        self.assertIn("qr:write", key_obj.scopes)


class AuditLogTests(TestCase):

    def setUp(self):
        self.user = make_active_user(email="audit@example.com")

    def test_create_audit_log(self):
        log = AuditLog.objects.create(
            user=self.user,
            action=AuditLog.Action.LOGIN,
            ip_address="127.0.0.1",
        )
        self.assertEqual(log.action, "login")
        self.assertEqual(log.user, self.user)

    def test_audit_log_user_can_be_null(self):
        log = AuditLog.objects.create(
            action=AuditLog.Action.LOGIN_FAILED,
            metadata={"email": "unknown@example.com"},
        )
        self.assertIsNone(log.user)


# ── Form Tests ────────────────────────────────────────────────────────────────

class SignupFormTests(TestCase):

    def _data(self, **overrides):
        data = {
            "full_name": "Bob Jones",
            "email": "bob@example.com",
            "password": "securepass99",
            "confirm_password": "securepass99",
            "agree_terms": True,
        }
        data.update(overrides)
        return data

    def test_valid_signup(self):
        form = SignupForm(data=self._data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_duplicate_email(self):
        make_active_user(email="bob@example.com")
        form = SignupForm(data=self._data())
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_password_mismatch(self):
        form = SignupForm(data=self._data(confirm_password="different99"))
        self.assertFalse(form.is_valid())

    def test_short_password(self):
        form = SignupForm(data=self._data(password="short", confirm_password="short"))
        self.assertFalse(form.is_valid())

    def test_agree_terms_required(self):
        form = SignupForm(data=self._data(agree_terms=False))
        self.assertFalse(form.is_valid())

    def test_save_creates_user(self):
        form = SignupForm(data=self._data())
        self.assertTrue(form.is_valid())
        user = form.save()
        self.assertEqual(user.email, "bob@example.com")
        self.assertFalse(user.is_active)


class LoginFormTests(TestCase):

    def test_valid_data(self):
        form = LoginForm(data={"email": "u@example.com", "password": "pass"})
        self.assertTrue(form.is_valid())

    def test_missing_email(self):
        form = LoginForm(data={"email": "", "password": "pass"})
        self.assertFalse(form.is_valid())

    def test_missing_password(self):
        form = LoginForm(data={"email": "u@example.com", "password": ""})
        self.assertFalse(form.is_valid())


class ResendVerificationFormTests(TestCase):

    def test_normalises_email(self):
        form = ResendVerificationForm(data={"email": "USER@Example.COM"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "user@example.com")


class ResetPasswordFormTests(TestCase):

    def test_valid_matching_passwords(self):
        form = ResetPasswordForm(data={"password": "newpass1234", "confirm_password": "newpass1234"})
        self.assertTrue(form.is_valid())

    def test_mismatch_passwords(self):
        form = ResetPasswordForm(data={"password": "newpass1234", "confirm_password": "different1234"})
        self.assertFalse(form.is_valid())

    def test_password_too_short(self):
        form = ResetPasswordForm(data={"password": "short", "confirm_password": "short"})
        self.assertFalse(form.is_valid())


# ── View Tests ────────────────────────────────────────────────────────────────

@override_settings(CACHES=CACHE_OVERRIDE)
class SignupViewTests(TestCase):

    def test_get_signup_page(self):
        resp = self.client.get(reverse("accounts:signup"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Continue with Google")
        self.assertContains(resp, "Dynamic links")

    def test_successful_signup_redirects_to_login_without_verification_email(self):
        resp = self.client.post(reverse("accounts:signup"), {
            "full_name": "Alice",
            "email": "alice@example.com",
            "password": "securepass99",
            "confirm_password": "securepass99",
            "agree_terms": True,
        })
        self.assertRedirects(resp, reverse("accounts:login"))
        user = User.objects.get(email="alice@example.com")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_email_verified)
        self.assertEqual(EmailVerificationToken.objects.filter(user=user).count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_signup_with_invalid_data_shows_form(self):
        resp = self.client.post(reverse("accounts:signup"), {
            "full_name": "",
            "email": "notvalid",
            "password": "x",
            "confirm_password": "y",
        })
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_user_redirected(self):
        user = make_active_user()
        self.client.force_login(user)
        resp = self.client.get(reverse("accounts:signup"))
        self.assertEqual(resp.status_code, 302)


@override_settings(CACHES=CACHE_OVERRIDE)
class LoginViewTests(TestCase):

    def setUp(self):
        self.user = make_active_user(password="testpass123")
        self.url = reverse("accounts:login")

    def test_get_login_page(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Continue with Google")
        self.assertContains(resp, "Forgot password?")

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_valid_login_redirects(self, mock_ip):
        resp = self.client.post(self.url, {
            "email": self.user.email,
            "password": "testpass123",
        })
        self.assertEqual(resp.status_code, 302)

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_valid_login_rejects_unsafe_next_url(self, mock_ip):
        resp = self.client.post(self.url + "?next=https://evil.example/", {
            "email": self.user.email,
            "password": "testpass123",
        })
        self.assertRedirects(resp, "/dashboard/", fetch_redirect_response=False)

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_remember_me_sets_seven_day_session(self, mock_ip):
        self.client.post(self.url, {
            "email": self.user.email,
            "password": "testpass123",
            "remember_me": "on",
        })
        self.assertGreaterEqual(self.client.session.get_expiry_age(), 60 * 60 * 24 * 6)

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_login_without_remember_me_expires_at_browser_close(self, mock_ip):
        self.client.post(self.url, {
            "email": self.user.email,
            "password": "testpass123",
        })
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_invalid_password_stays_on_page(self, mock_ip):
        resp = self.client.post(self.url, {
            "email": self.user.email,
            "password": "wrongpassword",
        })
        self.assertEqual(resp.status_code, 200)

    @override_settings(EMAIL_VERIFICATION_REQUIRED=True)
    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_unverified_email_blocked(self, mock_ip):
        self.user.is_email_verified = False
        self.user.save(update_fields=["is_email_verified"])
        resp = self.client.post(self.url, {
            "email": self.user.email,
            "password": "testpass123",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Resend verification email")

    def test_authenticated_user_redirected(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)


@override_settings(
    CACHES=CACHE_OVERRIDE,
    GOOGLE_OAUTH_CLIENT_ID="client-id.apps.googleusercontent.com",
    GOOGLE_OAUTH_CLIENT_SECRET="client-secret",
)
class GoogleOAuthViewTests(TestCase):

    def test_google_login_redirects_to_google_with_state(self):
        resp = self.client.get(reverse("accounts:google_login"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("accounts.google.com", resp["Location"])
        self.assertIn("scope=openid+email+profile", resp["Location"])
        self.assertIn("google_oauth_state", self.client.session)

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_CLIENT_SECRET="")
    def test_google_login_without_config_redirects_to_login(self):
        resp = self.client.get(reverse("accounts:google_login"))
        self.assertRedirects(resp, reverse("accounts:login"))

    @patch("apps.accounts.views.requests.get")
    @patch("apps.accounts.views.requests.post")
    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_google_callback_creates_verified_user(self, mock_ip, mock_post, mock_get):
        session = self.client.session
        session["google_oauth_state"] = "state123"
        session["google_oauth_next"] = "/dashboard/"
        session.save()

        token_response = MagicMock()
        token_response.json.return_value = {"id_token": "id-token"}
        token_response.raise_for_status.return_value = None
        mock_post.return_value = token_response

        profile_response = MagicMock()
        profile_response.json.return_value = {
            "aud": "client-id.apps.googleusercontent.com",
            "sub": "google-sub-1",
            "email": "newgoogle@example.com",
            "email_verified": "true",
            "name": "Google User",
        }
        profile_response.raise_for_status.return_value = None
        mock_get.return_value = profile_response

        resp = self.client.get(reverse("accounts:google_callback"), {"state": "state123", "code": "abc"})

        self.assertRedirects(resp, "/dashboard/", fetch_redirect_response=False)
        user = User.objects.get(email="newgoogle@example.com")
        self.assertEqual(user.google_sub, "google-sub-1")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_email_verified)
        self.assertFalse(user.has_usable_password())

    @patch("apps.accounts.views.requests.get")
    @patch("apps.accounts.views.requests.post")
    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_google_callback_links_existing_verified_email(self, mock_ip, mock_post, mock_get):
        user = make_active_user(email="existinggoogle@example.com")
        session = self.client.session
        session["google_oauth_state"] = "state123"
        session.save()

        token_response = MagicMock()
        token_response.json.return_value = {"id_token": "id-token"}
        token_response.raise_for_status.return_value = None
        mock_post.return_value = token_response

        profile_response = MagicMock()
        profile_response.json.return_value = {
            "aud": "client-id.apps.googleusercontent.com",
            "sub": "google-sub-2",
            "email": user.email,
            "email_verified": True,
            "name": "Existing Google",
        }
        profile_response.raise_for_status.return_value = None
        mock_get.return_value = profile_response

        resp = self.client.get(reverse("accounts:google_callback"), {"state": "state123", "code": "abc"})

        self.assertRedirects(resp, "/dashboard/", fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertEqual(user.google_sub, "google-sub-2")


@override_settings(CACHES=CACHE_OVERRIDE)
class LogoutViewTests(TestCase):

    def setUp(self):
        self.user = make_active_user()

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_logout_redirects_to_login(self, mock_ip):
        self.client.force_login(self.user)
        resp = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(resp, reverse("accounts:login"))

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("accounts:logout"))
        self.assertEqual(resp.status_code, 405)


@override_settings(CACHES=CACHE_OVERRIDE)
class VerifyEmailViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(email="v@example.com", password="pass123456")

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_valid_token_activates_user(self, mock_ip):
        tok = EmailVerificationToken.objects.create(
            user=self.user,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        resp = self.client.get(reverse("accounts:verify_email", args=[tok.token]))
        self.assertRedirects(resp, reverse("accounts:login"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(self.user.is_email_verified)

    def test_invalid_token_returns_404(self):
        resp = self.client.get(reverse("accounts:verify_email", args=["nonexistenttoken"]))
        self.assertEqual(resp.status_code, 404)

    def test_expired_token_redirects_to_login(self):
        tok = EmailVerificationToken.objects.create(
            user=self.user,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        resp = self.client.get(reverse("accounts:verify_email", args=[tok.token]))
        self.assertRedirects(resp, reverse("accounts:login"))


@override_settings(CACHES=CACHE_OVERRIDE)
class ResendVerificationViewTests(TestCase):

    def test_resend_when_verification_disabled_does_not_send_email(self):
        user = User.objects.create_user(email="pending@example.com", password="pass123456")
        resp = self.client.post(reverse("accounts:resend_verification"), {"email": user.email})

        self.assertRedirects(resp, reverse("accounts:login"))
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailVerificationToken.objects.filter(user=user).exists())

    def test_resend_for_unknown_email_does_not_reveal(self):
        resp = self.client.post(reverse("accounts:resend_verification"), {"email": "missing@example.com"})

        self.assertRedirects(resp, reverse("accounts:login"))
        self.assertEqual(len(mail.outbox), 0)


@override_settings(CACHES=CACHE_OVERRIDE)
class ForgotPasswordViewTests(TestCase):

    def test_get_forgot_password_page(self):
        resp = self.client.get(reverse("accounts:forgot_password"))
        self.assertEqual(resp.status_code, 200)

    @patch("apps.accounts.views._send_password_reset_email")
    def test_post_with_existing_email_redirects(self, mock_send):
        user = make_active_user(email="forgot@example.com")
        mock_send.return_value = True
        resp = self.client.post(reverse("accounts:forgot_password"), {"email": "forgot@example.com"})
        self.assertRedirects(resp, reverse("accounts:forgot_password"))
        mock_send.assert_called_once()

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="support@example.com",
    )
    def test_post_with_existing_email_sends_reset_link(self):
        user = make_active_user(email="reset-link@example.com")
        old_token = PasswordResetToken.objects.create(user=user)

        resp = self.client.post(reverse("accounts:forgot_password"), {"email": user.email})

        self.assertRedirects(resp, reverse("accounts:forgot_password"))
        self.assertEqual(len(mail.outbox), 1)
        old_token.refresh_from_db()
        self.assertTrue(old_token.is_used)
        token = PasswordResetToken.objects.get(user=user, is_used=False)
        reset_path = reverse("accounts:reset_password", args=[token.token])
        self.assertIn(reset_path, mail.outbox[0].body)
        self.assertIn(reset_path, mail.outbox[0].alternatives[0][0])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_post_when_email_backend_cannot_deliver_shows_error(self):
        make_active_user(email="console@example.com")

        resp = self.client.post(reverse("accounts:forgot_password"), {"email": "console@example.com"}, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Password reset email delivery is not configured yet")
        self.assertEqual(len(mail.outbox), 0)

    def test_post_with_nonexistent_email_does_not_reveal(self):
        resp = self.client.post(reverse("accounts:forgot_password"), {"email": "nobody@example.com"})
        self.assertRedirects(resp, reverse("accounts:forgot_password"))


@override_settings(CACHES=CACHE_OVERRIDE)
class ResetPasswordViewTests(TestCase):

    def setUp(self):
        self.user = make_active_user(email="rp@example.com")
        self.token = PasswordResetToken.objects.create(
            user=self.user,
            expires_at=timezone.now() + timedelta(hours=2),
        )

    def test_get_reset_page_with_valid_token(self):
        resp = self.client.get(reverse("accounts:reset_password", args=[self.token.token]))
        self.assertEqual(resp.status_code, 200)

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_valid_reset_changes_password(self, mock_ip):
        resp = self.client.post(
            reverse("accounts:reset_password", args=[self.token.token]),
            {"password": "newpassword99", "confirm_password": "newpassword99"},
        )
        self.assertRedirects(resp, reverse("accounts:login"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpassword99"))

    def test_invalid_token_returns_404(self):
        resp = self.client.get(reverse("accounts:reset_password", args=["badtoken"]))
        self.assertEqual(resp.status_code, 404)

    def test_expired_token_redirects(self):
        self.token.expires_at = timezone.now() - timedelta(hours=1)
        self.token.save()
        resp = self.client.get(reverse("accounts:reset_password", args=[self.token.token]))
        self.assertRedirects(resp, reverse("accounts:forgot_password"))


@override_settings(CACHES=CACHE_OVERRIDE)
class ProfileViewTests(TestCase):

    def setUp(self):
        self.user = make_active_user(email="profile@example.com")
        self.client.force_login(self.user)

    def test_get_profile_page(self):
        resp = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Business Verification")
        self.assertContains(resp, "Verify your official domain")

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_update_profile(self, mock_ip):
        resp = self.client.post(reverse("accounts:profile"), {
            "company": "Acme Corp",
            "website": "https://acme.com",
            "timezone": "UTC",
            "phone": "+1-555-0100",
            "brand_color": "#ff0000",
            "scan_alert_threshold": "100",
        })
        self.assertRedirects(resp, reverse("accounts:profile"))
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.company, "Acme Corp")

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_change_password(self, mock_ip):
        resp = self.client.post(reverse("accounts:change_password"), {
            "current_password": "testpass123",
            "new_password": "newpassword99",
            "confirm_password": "newpassword99",
        })
        self.assertRedirects(resp, reverse("accounts:profile"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpassword99"))

    def test_change_password_rejects_wrong_current_password(self):
        resp = self.client.post(reverse("accounts:change_password"), {
            "current_password": "wrongpass123",
            "new_password": "newpassword99",
            "confirm_password": "newpassword99",
        })
        self.assertRedirects(resp, reverse("accounts:profile"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("testpass123"))

    def test_unauthenticated_redirects(self):
        self.client.logout()
        resp = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp.status_code, 302)

    def test_business_verification_form_normalizes_domain(self):
        form = BusinessVerificationForm({
            "business_name": "Acme Foods",
            "domain": "https://Example.COM/menu",
            "method": BusinessVerification.Method.DNS,
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["domain"], "example.com")

    def test_business_verification_form_rejects_localhost(self):
        form = BusinessVerificationForm({
            "business_name": "Local",
            "domain": "localhost",
            "method": BusinessVerification.Method.HTML_FILE,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("domain", form.errors)

    def test_save_business_verification_creates_pending_setup(self):
        resp = self.client.post(reverse("accounts:save_business_verification"), {
            "business_name": "Acme Foods",
            "domain": "acme.example",
            "method": BusinessVerification.Method.META_TAG,
        })
        self.assertRedirects(resp, f"{reverse('accounts:profile')}#verification")
        verification = BusinessVerification.objects.get(workspace=self.user)
        self.assertEqual(verification.business_name, "Acme Foods")
        self.assertEqual(verification.domain, "acme.example")
        self.assertEqual(verification.status, BusinessVerification.Status.PENDING)
        self.assertTrue(verification.verification_token)

    @patch("apps.accounts.views.verify_business_domain", return_value=(True, "DNS TXT record matched."))
    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_verify_business_success_sets_verified(self, mock_ip, mock_verify):
        verification = BusinessVerification.objects.create(
            workspace=self.user,
            business_name="Acme Foods",
            domain="acme.example",
            method=BusinessVerification.Method.DNS,
        )
        resp = self.client.post(reverse("accounts:verify_business_verification"))
        self.assertRedirects(resp, f"{reverse('accounts:profile')}#verification")
        verification.refresh_from_db()
        self.assertEqual(verification.status, BusinessVerification.Status.VERIFIED)
        self.assertIsNotNone(verification.verified_at)
        attempt = BusinessVerificationAttempt.objects.get(verification=verification)
        self.assertTrue(attempt.success)
        mock_verify.assert_called_once_with(verification)

    @patch("apps.accounts.views.verify_business_domain", return_value=(False, "Token not found."))
    def test_verify_business_failure_sets_failed(self, mock_verify):
        verification = BusinessVerification.objects.create(
            workspace=self.user,
            business_name="Acme Foods",
            domain="acme.example",
            method=BusinessVerification.Method.HTML_FILE,
        )
        resp = self.client.post(reverse("accounts:verify_business_verification"))
        self.assertRedirects(resp, f"{reverse('accounts:profile')}#verification")
        verification.refresh_from_db()
        self.assertEqual(verification.status, BusinessVerification.Status.FAILED)
        self.assertIsNone(verification.verified_at)


@override_settings(CACHES=CACHE_OVERRIDE)
class APIKeyViewTests(TestCase):

    def setUp(self):
        self.user = make_active_user(email="apikey@example.com")
        self.user.plan = "pro"
        self.user.role = User.Role.PRO
        self.user.save(update_fields=["plan", "role"])
        self.client.force_login(self.user)

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_create_api_key(self, mock_ip):
        resp = self.client.post(reverse("accounts:create_api_key"), {"name": "My Key"})
        self.assertRedirects(resp, reverse("accounts:profile"))
        self.assertEqual(APIKey.objects.filter(user=self.user).count(), 1)

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_create_api_key_without_name_fails(self, mock_ip):
        resp = self.client.post(reverse("accounts:create_api_key"), {"name": ""})
        self.assertRedirects(resp, reverse("accounts:profile"))
        self.assertEqual(APIKey.objects.filter(user=self.user).count(), 0)

    @patch("apps.accounts.views.get_client_ip", return_value="127.0.0.1")
    def test_revoke_api_key(self, mock_ip):
        key_obj, _ = APIKey.generate(self.user, "Revokable")
        resp = self.client.post(reverse("accounts:revoke_api_key", args=[key_obj.id]))
        self.assertRedirects(resp, reverse("accounts:profile"))
        key_obj.refresh_from_db()
        self.assertEqual(key_obj.status, APIKey.Status.REVOKED)

    def test_revoke_other_users_key_returns_404(self):
        other = make_active_user(email="other@example.com")
        key_obj, _ = APIKey.generate(other, "Other Key")
        resp = self.client.post(reverse("accounts:revoke_api_key", args=[key_obj.id]))
        self.assertEqual(resp.status_code, 404)


@override_settings(
    CACHES=CACHE_OVERRIDE,
    PAYTM_ENVIRONMENT="staging",
    PAYTM_MID="TESTMID123",
    PAYTM_MERCHANT_KEY="testmerchantkey1",
    PAYTM_WEBSITE_NAME="WEBSTAGING",
)
class MembershipBillingTests(TestCase):

    def setUp(self):
        self.user = make_active_user(email="billing@example.com")
        self.client.force_login(self.user)

    @patch("apps.accounts.billing_views.create_paytm_transaction")
    def test_checkout_creates_membership_order(self, mock_create_transaction):
        mock_create_transaction.return_value = {
            "body": {"txnToken": "txn_token_123", "resultInfo": {"resultStatus": "S"}},
            "head": {},
        }

        resp = self.client.get(reverse("accounts:billing_checkout", args=["pro"]) + "?billing=monthly")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Pay with Paytm / UPI")
        self.assertContains(resp, "txn_token_123")
        order = MembershipOrder.objects.get(provider="paytm")
        self.assertEqual(order.plan_code, "pro")
        self.assertEqual(order.amount_paise, 49900)
        mock_create_transaction.assert_called_once()

    @override_settings(PAYTM_MID="", PAYTM_MERCHANT_KEY="")
    def test_checkout_requires_gateway_configuration(self):
        resp = self.client.get(reverse("accounts:billing_checkout", args=["pro"]))

        self.assertEqual(resp.status_code, 503)
        self.assertContains(resp, "Paytm is not configured", status_code=503)

    @patch("apps.accounts.billing_views.fetch_paytm_transaction_status")
    def test_verified_callback_activates_membership(self, mock_status):
        import paytmchecksum

        order = MembershipOrder.objects.create(
            user=self.user,
            plan_code="pro",
            billing_cycle="monthly",
            amount_paise=49900,
            provider="paytm",
            provider_order_id="ORDER_VERIFIED",
            receipt="receipt_verified",
        )
        mock_status.return_value = {
            "body": {
                "txnId": "TXN_VERIFIED",
                "resultInfo": {"resultStatus": "TXN_SUCCESS", "resultCode": "01"},
            }
        }
        params = {
            "ORDERID": order.provider_order_id,
            "TXNID": "TXN_VERIFIED",
            "STATUS": "TXN_SUCCESS",
            "RESPCODE": "01",
        }
        params["CHECKSUMHASH"] = paytmchecksum.generateSignature(params, "testmerchantkey1")

        resp = self.client.post(reverse("accounts:billing_callback"), params)

        self.assertRedirects(resp, reverse("qrcodes:dashboard"))
        self.user.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.user.active_plan_code, "pro")
        self.assertEqual(order.status, MembershipOrder.Status.PAID)

    def test_invalid_callback_does_not_activate_membership(self):
        order = MembershipOrder.objects.create(
            user=self.user,
            plan_code="pro",
            billing_cycle="monthly",
            amount_paise=49900,
            provider="paytm",
            provider_order_id="ORDER_INVALID",
            receipt="receipt_invalid",
        )

        resp = self.client.post(reverse("accounts:billing_callback"), {
            "ORDERID": order.provider_order_id,
            "TXNID": "TXN_INVALID",
            "STATUS": "TXN_FAILURE",
            "CHECKSUMHASH": "bad",
        })

        self.assertRedirects(resp, reverse("core:pricing"))
        self.user.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.user.active_plan_code, "free")
        self.assertEqual(order.status, MembershipOrder.Status.FAILED)
