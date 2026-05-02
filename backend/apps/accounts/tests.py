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
    UserProfile, EmailVerificationToken, PasswordResetToken, APIKey, AuditLog
)
from apps.accounts.forms import (
    SignupForm, LoginForm, ProfileForm, ChangePasswordForm,
    ForgotPasswordForm, ResetPasswordForm, ResendVerificationForm,
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

    def test_update_last_activity(self):
        self.user.update_last_activity()
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_activity)

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

    @patch("apps.accounts.views._send_verification_email")
    def test_successful_signup_redirects_to_login(self, mock_send):
        resp = self.client.post(reverse("accounts:signup"), {
            "full_name": "Alice",
            "email": "alice@example.com",
            "password": "securepass99",
            "confirm_password": "securepass99",
            "agree_terms": True,
        })
        self.assertRedirects(resp, reverse("accounts:login"))
        self.assertTrue(User.objects.filter(email="alice@example.com").exists())

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

    def test_resend_for_unverified_user_sends_email(self):
        user = User.objects.create_user(email="pending@example.com", password="pass123456")
        resp = self.client.post(reverse("accounts:resend_verification"), {"email": user.email})

        self.assertRedirects(resp, reverse("accounts:login"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(EmailVerificationToken.objects.filter(user=user).exists())

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
        resp = self.client.post(reverse("accounts:forgot_password"), {"email": "forgot@example.com"})
        self.assertRedirects(resp, reverse("accounts:forgot_password"))
        mock_send.assert_called_once()

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


@override_settings(CACHES=CACHE_OVERRIDE)
class APIKeyViewTests(TestCase):

    def setUp(self):
        self.user = make_active_user(email="apikey@example.com")
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
