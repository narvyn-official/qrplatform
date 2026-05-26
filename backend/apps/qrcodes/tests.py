"""
Comprehensive tests for the qrcodes app.
Covers: models, forms, and views.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import BusinessVerification
from apps.qrcodes.models import (
    Certificate,
    QRCode,
    QRCodeCampaign,
    QRScanEvent,
    QRDestinationRule,
    QRLandingPage,
    QRConversionEvent,
    QRSuspiciousReport,
    QRScannerHistory,
)
from apps.qrcodes.forms import QRCodeForm
from apps.qrcodes.safety import analyze_scanned_value, analyze_scanner_content, analyze_url
from apps.qrcodes.views import _qrcode_form_data

User = get_user_model()

CACHE_OVERRIDE = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}


def make_active_user(email="qr@example.com", password="testpass123", **kw):
    user = User.objects.create_user(
        email=email, password=password,
        full_name=kw.pop("full_name", "QR Tester"), **kw,
    )
    user.is_active = True
    user.is_email_verified = True
    user.save(update_fields=["is_active", "is_email_verified"])
    return user


def make_qrcode(user, **kw):
    defaults = {
        "name": "Test QR",
        "qr_type": QRCode.QRType.URL,
        "content": "https://example.com",
    }
    defaults.update(kw)
    return QRCode.objects.create(user=user, **defaults)


def make_certificate(user, **kw):
    defaults = {
        "workspace": user,
        "recipient_name": "Aarav Sharma",
        "title": "Certificate of Completion",
        "issuer": "Narvyn Academy",
        "issue_date": timezone.localdate(),
        "certificate_id": f"CERT-{uuid.uuid4().hex[:8].upper()}",
    }
    defaults.update(kw)
    return Certificate.objects.create(**defaults)


# ── Model tests ───────────────────────────────────────────────────────────────

class QRCodeModelTest(TestCase):

    def setUp(self):
        self.user = make_active_user()

    def test_short_code_auto_generated(self):
        qr = make_qrcode(self.user)
        self.assertIsNotNone(qr.short_code)
        self.assertTrue(len(qr.short_code) >= 6)

    def test_short_code_unique(self):
        qr1 = make_qrcode(self.user, name="QR1")
        qr2 = make_qrcode(self.user, name="QR2")
        self.assertNotEqual(qr1.short_code, qr2.short_code)

    def test_str(self):
        qr = make_qrcode(self.user, name="MyQR")
        self.assertIn("MyQR", str(qr))

    def test_is_dynamic_true(self):
        qr = make_qrcode(
            self.user, qr_type=QRCode.QRType.DYNAMIC,
            content="https://dest.com", destination_url="https://dest.com",
        )
        self.assertTrue(qr.is_dynamic)

    def test_is_dynamic_false(self):
        qr = make_qrcode(self.user)
        self.assertFalse(qr.is_dynamic)

    def test_encoded_content_url_uses_tracking_redirect(self):
        qr = make_qrcode(self.user, content="https://example.com")
        self.assertIn(qr.short_code, qr.encoded_content)

    def test_encoded_content_vcard_uses_tracking_redirect(self):
        qr = make_qrcode(self.user, qr_type=QRCode.QRType.VCARD, content="BEGIN:VCARD\nFN:Test\nEND:VCARD")
        self.assertEqual(qr.encoded_content, qr.redirect_url)

    def test_encoded_content_dynamic_uses_redirect_url(self):
        qr = make_qrcode(
            self.user, qr_type=QRCode.QRType.DYNAMIC,
            content="https://dest.com", destination_url="https://dest.com",
        )
        self.assertIn(qr.short_code, qr.encoded_content)

    def test_is_expired_false_by_default(self):
        qr = make_qrcode(self.user)
        self.assertFalse(qr.is_expired)

    def test_is_expired_by_date(self):
        past = timezone.now() - timedelta(hours=1)
        qr = make_qrcode(self.user, expires_at=past)
        self.assertTrue(qr.is_expired)

    def test_is_expired_false_future_date(self):
        future = timezone.now() + timedelta(days=7)
        qr = make_qrcode(self.user, expires_at=future)
        self.assertFalse(qr.is_expired)

    def test_is_expired_by_scan_limit(self):
        qr = make_qrcode(self.user, scan_limit=5, total_scans=5)
        self.assertTrue(qr.is_expired)

    def test_is_expired_not_by_scan_limit_below(self):
        qr = make_qrcode(self.user, scan_limit=10, total_scans=5)
        self.assertFalse(qr.is_expired)

    def test_increment_scan_total(self):
        qr = make_qrcode(self.user)
        qr.increment_scan(is_unique=False)
        qr.refresh_from_db()
        self.assertEqual(qr.total_scans, 1)
        self.assertEqual(qr.unique_scans, 0)

    def test_increment_scan_unique(self):
        qr = make_qrcode(self.user)
        qr.increment_scan(is_unique=True)
        qr.refresh_from_db()
        self.assertEqual(qr.total_scans, 1)
        self.assertEqual(qr.unique_scans, 1)

    def test_set_and_check_access_password(self):
        qr = make_qrcode(self.user)
        qr.set_access_password("secret123")
        self.assertTrue(qr.check_access_password("secret123"))
        self.assertFalse(qr.check_access_password("wrongpass"))


class QRCodeCampaignTest(TestCase):

    def setUp(self):
        self.user = make_active_user()

    def test_str(self):
        campaign = QRCodeCampaign.objects.create(user=self.user, name="Summer Campaign")
        self.assertIn("Summer Campaign", str(campaign))

    def test_total_scans_aggregates(self):
        campaign = QRCodeCampaign.objects.create(user=self.user, name="Test Campaign")
        make_qrcode(self.user, campaign=campaign, total_scans=10)
        make_qrcode(self.user, campaign=campaign, total_scans=20, name="QR2")
        self.assertEqual(campaign.total_scans, 30)

    def test_total_scans_empty(self):
        campaign = QRCodeCampaign.objects.create(user=self.user, name="Empty Campaign")
        self.assertEqual(campaign.total_scans, 0)


class QRScanEventTest(TestCase):

    def setUp(self):
        self.user = make_active_user()

    def test_str(self):
        qr = make_qrcode(self.user)
        event = QRScanEvent.objects.create(
            qrcode=qr, ip_address="1.2.3.4",
            fingerprint="abc123",
        )
        self.assertIn(qr.short_code, str(event))


# ── Form tests ────────────────────────────────────────────────────────────────

class QRCodeFormTest(TestCase):

    def setUp(self):
        self.user = make_active_user()

    # Minimal valid data shared by all form tests
    BASE_FORM_DATA = {
        "tags": "[]",
        "foreground_color": "#000000",
        "background_color": "#FFFFFF",
        "dot_style": "square",
        "corner_style": "square",
        "outer_shape": "square",
        "logo_size_ratio": "0.2",
        "frame_color": "#000000",
        "frame_text": "",
        "error_correction": "M",
        "qr_size": "300",
    }

    def test_valid_url_form(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "My URL QR",
            "qr_type": "url",
            "content": "https://example.com",
        }, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)

    def test_url_prepends_https(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "No scheme",
            "qr_type": "url",
            "content": "example.com",
        }, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["content"].startswith("https://"))

    def test_url_rejects_invalid_http_url(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Bad URL",
            "qr_type": "url",
            "content": "not a url",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("content", form.errors)

    def test_url_rejects_non_http_scheme(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Bad scheme",
            "qr_type": "url",
            "content": "javascript:alert(1)",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("content", form.errors)

    def test_dynamic_requires_destination_url(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Dynamic QR",
            "qr_type": "dynamic",
            "content": "",
            "destination_url": "",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("destination_url", form.errors)

    def test_dynamic_valid_with_destination(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Dynamic QR",
            "qr_type": "dynamic",
            "content": "",
            "destination_url": "https://example.com/dest",
        }, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)

    def test_dynamic_prepends_https_to_destination(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Dynamic QR",
            "qr_type": "dynamic",
            "content": "",
            "destination_url": "example.com/dest",
        }, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["destination_url"], "https://example.com/dest")
        self.assertEqual(form.cleaned_data["content"], "https://example.com/dest")

    def test_dynamic_rejects_invalid_destination_url(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Dynamic QR",
            "qr_type": "dynamic",
            "destination_url": "bad url",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("destination_url", form.errors)

    def test_text_requires_content(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Text QR",
            "qr_type": "text",
            "content": "",
        }, user=self.user)
        self.assertFalse(form.is_valid())

    def test_whatsapp_normalizes_to_wa_me_url(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "WhatsApp QR",
            "qr_type": "whatsapp",
            "content": "+1 555 000 0000",
        }, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["content"], "https://wa.me/15550000000")

    def test_whatsapp_rejects_invalid_phone(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "WhatsApp QR",
            "qr_type": "whatsapp",
            "content": "call me later",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("content", form.errors)

    def test_sms_rejects_invalid_phone(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "SMS QR",
            "qr_type": "sms",
            "content": "abc|Hello",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("content", form.errors)

    def test_email_rejects_invalid_address(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Email QR",
            "qr_type": "email",
            "content": "bad@",
        }, user=self.user)
        self.assertFalse(form.is_valid())

    def test_invalid_hex_color_is_rejected(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Color QR",
            "qr_type": "url",
            "content": "https://example.com",
            "foreground_color": "black",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("foreground_color", form.errors)

    def test_foreground_and_background_must_differ(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Invisible QR",
            "qr_type": "url",
            "content": "https://example.com",
            "foreground_color": "#000000",
            "background_color": "#000000",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("background_color", form.errors)

    def test_qr_size_is_bounded(self):
        for size in ("64", "4096"):
            form = QRCodeForm({
                **self.BASE_FORM_DATA,
                "name": f"Size {size}",
                "qr_type": "url",
                "content": "https://example.com",
                "qr_size": size,
            }, user=self.user)
            self.assertFalse(form.is_valid())
            self.assertIn("qr_size", form.errors)

    def test_logo_ratio_is_bounded(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Huge logo",
            "qr_type": "url",
            "content": "https://example.com",
            "logo_size_ratio": "0.6",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("logo_size_ratio", form.errors)

    def test_expiry_must_be_future(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Past expiry",
            "qr_type": "url",
            "content": "https://example.com",
            "expires_at": timezone.now() - timedelta(hours=1),
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("expires_at", form.errors)

    def test_scheduled_end_must_be_after_start(self):
        self.user.plan = "pro"
        self.user.role = User.Role.PRO
        self.user.plan_expires_at = timezone.now() + timedelta(days=5)
        self.user.save(update_fields=["plan", "role", "plan_expires_at"])
        start = timezone.now() + timedelta(days=2)
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Bad schedule",
            "qr_type": "url",
            "content": "https://example.com",
            "scheduled_active_from": start,
            "scheduled_active_until": start - timedelta(hours=1),
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("scheduled_active_until", form.errors)

    def test_free_plan_rejects_paid_only_fields(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Premium shape",
            "qr_type": "url",
            "content": "https://example.com",
            "outer_shape": "circle",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("outer_shape", form.errors)

        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "UTM",
            "qr_type": "url",
            "content": "https://example.com",
            "utm_source": "poster",
        }, user=self.user)
        self.assertFalse(form.is_valid())

    def test_free_plan_rejects_scan_limit_above_plan(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Too many scans",
            "qr_type": "url",
            "content": "https://example.com",
            "scan_limit": "1001",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("scan_limit", form.errors)

    def test_expired_paid_plan_uses_free_limits(self):
        self.user.plan = "pro"
        self.user.role = User.Role.PRO
        self.user.plan_expires_at = timezone.now() - timedelta(days=1)
        self.user.save(update_fields=["plan", "role", "plan_expires_at"])

        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Expired paid shape",
            "qr_type": "url",
            "content": "https://example.com",
            "outer_shape": "circle",
        }, user=self.user)

        self.assertEqual(self.user.active_plan_code, "free")
        self.assertFalse(form.is_valid())
        self.assertIn("outer_shape", form.errors)

    def test_scan_limit_cannot_be_below_current_scans_on_edit(self):
        qr = make_qrcode(self.user, total_scans=10)
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": qr.name,
            "qr_type": "url",
            "content": "https://example.com",
            "scan_limit": "5",
        }, instance=qr, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("scan_limit", form.errors)

    def test_password_protection_requires_password_for_new_qr(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Protected QR",
            "qr_type": "url",
            "content": "https://example.com",
            "is_password_protected": "on",
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("access_password", form.errors)

    def test_password_saved_as_hash(self):
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Protected QR",
            "qr_type": "url",
            "content": "https://example.com",
            "is_password_protected": "on",
            "access_password": "secret123",
        }, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        qr = form.save(commit=False)
        self.assertNotEqual(qr.access_password_hash, "secret123")
        self.assertTrue(qr.check_access_password("secret123"))

    def test_legacy_duplicate_content_fields_keep_first_non_empty_value(self):
        data = QueryDict("", mutable=True)
        data.update({**self.BASE_FORM_DATA, "name": "URL QR", "qr_type": "url"})
        data.setlist("content", ["example.com", "", "", ""])
        normalized = _qrcode_form_data(data)
        self.assertEqual(normalized["content"], "example.com")

    def test_logo_size_limit(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        large = SimpleUploadedFile("logo.png", b"x" * (3 * 1024 * 1024), content_type="image/png")
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Logo QR", "qr_type": "url", "content": "https://example.com",
        }, files={"logo": large}, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)

    def test_logo_invalid_extension(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        bad_file = SimpleUploadedFile("logo.exe", b"bad data", content_type="application/octet-stream")
        form = QRCodeForm({
            **self.BASE_FORM_DATA,
            "name": "Logo QR", "qr_type": "url", "content": "https://example.com",
        }, files={"logo": bad_file}, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)


# ── View tests ────────────────────────────────────────────────────────────────

@override_settings(
    CACHES=CACHE_OVERRIDE,
    CELERY_TASK_ALWAYS_EAGER=True,
    AXES_ENABLED=False,
)
class DashboardViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()
        self.client.force_login(self.user)

    def test_dashboard_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("qrcodes:dashboard"))
        self.assertRedirects(resp, f"{reverse('accounts:login')}?next={reverse('qrcodes:dashboard')}")

    def test_scan_page_renders(self):
        resp = self.client.get(reverse("qrcodes:scan"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Scan safely before you open")
        self.assertContains(resp, "Result opens as a popup")
        self.assertContains(resp, "Report suspicious QR")
        self.assertContains(resp, "Scanned successfully")

    def test_dashboard_renders(self):
        resp = self.client.get(reverse("qrcodes:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("quick_actions", resp.context)
        self.assertIn("launch_checklist", resp.context)
        self.assertEqual(resp.context["completed_steps"], 0)
        self.assertEqual(resp.context["recommended_action"]["label"], "Create your first QR")
        self.assertContains(resp, "Campaign launch checklist")
        self.assertContains(resp, "Launch trusted QR campaigns.")
        self.assertContains(resp, reverse("accounts:logout"))
        self.assertContains(resp, "Logout")


    def test_dashboard_recommends_scan_after_first_qr(self):
        make_qrcode(self.user, name="Needs scan")

        resp = self.client.get(reverse("qrcodes:dashboard"))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["recommended_action"]["label"], "Test a scan now")
        self.assertContains(resp, "Recommended now: Test a scan now")

    def test_dashboard_recommends_upgrade_after_scan_for_free_user(self):
        make_qrcode(self.user, name="Scanned QR", total_scans=3, unique_scans=2)

        resp = self.client.get(reverse("qrcodes:dashboard"))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["recommended_action"]["label"], "Upgrade for campaigns")
        self.assertContains(resp, "branded exports, UTM tracking, schedules, and higher limits")

    def test_dashboard_shows_recent_qrcodes(self):
        make_qrcode(self.user, name="Recent QR")
        resp = self.client.get(reverse("qrcodes:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("recent_qrcodes", resp.context)

    def test_dashboard_uses_processed_scan_events_for_weekly_totals(self):
        qr = make_qrcode(self.user, name="Scanned QR")
        QRScanEvent.objects.create(
            qrcode=qr,
            ip_address="127.0.0.1",
            user_agent_raw="Mozilla/5.0",
            fingerprint="scan-1",
            is_unique=True,
            is_processed=True,
            device_type="desktop",
        )
        QRScanEvent.objects.create(
            qrcode=qr,
            ip_address="127.0.0.1",
            user_agent_raw="Mozilla/5.0",
            fingerprint="scan-2",
            is_unique=False,
            is_processed=True,
            device_type="desktop",
        )

        resp = self.client.get(reverse("qrcodes:dashboard"))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(sum(row["total_scans"] for row in resp.context["weekly_stats"]), 2)
        self.assertEqual(sum(row["unique_scans"] for row in resp.context["weekly_stats"]), 1)
        self.assertEqual(resp.context["weekly_total_scans"], 2)
        self.assertEqual(resp.context["weekly_unique_scans"], 1)
        self.assertContains(resp, 'id="weeklyTotal">2</p>')
        self.assertContains(resp, 'id="weeklyUnique">1</p>')

    def test_dashboard_total_scans_include_expired_codes(self):
        make_qrcode(self.user, name="Expired QR", status=QRCode.Status.EXPIRED, total_scans=8)

        resp = self.client.get(reverse("qrcodes:dashboard"))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total_scans"], 8)

    def test_dashboard_exposes_all_time_unique_scans(self):
        make_qrcode(self.user, name="Unique QR", total_scans=5, unique_scans=3)

        resp = self.client.get(reverse("qrcodes:dashboard"))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total_scans"], 5)
        self.assertEqual(resp.context["unique_scans"], 3)


class CertificateVerificationViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user(email="certs@example.com")
        self.client.force_login(self.user)

    @patch("apps.qrcodes.views._generate_qr_now", return_value=True)
    def test_certificate_create_generates_verification_qr(self, mock_generate):
        resp = self.client.post(reverse("qrcodes:certificate_new"), {
            "recipient_name": "Priya Mehta",
            "title": "Advanced Python Training",
            "issuer": "Narvyn Institute",
            "issue_date": timezone.localdate().isoformat(),
            "certificate_id": "CERT-2026-001",
        })

        certificate = Certificate.objects.get(certificate_id="CERT-2026-001")
        self.assertRedirects(resp, reverse("qrcodes:certificate_detail", kwargs={"pk": certificate.pk}))
        self.assertEqual(certificate.workspace, self.user)
        self.assertIsNotNone(certificate.qrcode)
        self.assertEqual(certificate.qrcode.qr_type, QRCode.QRType.DYNAMIC)
        self.assertIn(certificate.verification_code, certificate.qrcode.destination_url)
        mock_generate.assert_called_once_with(certificate.qrcode)

    def test_certificate_form_validates_required_fields(self):
        resp = self.client.post(reverse("qrcodes:certificate_new"), {
            "recipient_name": "",
            "title": "Completion",
            "issuer": "Narvyn Institute",
            "issue_date": timezone.localdate().isoformat(),
            "certificate_id": "CERT-MISSING-NAME",
        })

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "This field is required")
        self.assertFalse(Certificate.objects.exists())

    @patch("apps.qrcodes.views._generate_qr_now", return_value=True)
    def test_certificate_bulk_upload_creates_records_and_qrs(self, mock_generate):
        csv_file = SimpleUploadedFile(
            "certificates.csv",
            (
                "recipient_name,title,issuer,issue_date,certificate_id,expiry_date\n"
                "Aarav Sharma,Data Science Bootcamp,Narvyn Academy,2026-05-01,CERT-BULK-1,\n"
                "Meera Rao,Design Workshop,Narvyn Academy,2026-05-02,CERT-BULK-2,2027-05-02\n"
            ).encode(),
            content_type="text/csv",
        )

        resp = self.client.post(reverse("qrcodes:certificate_bulk_upload"), {"csv_file": csv_file})

        self.assertRedirects(resp, reverse("qrcodes:certificate_list"))
        self.assertEqual(Certificate.objects.count(), 2)
        self.assertEqual(QRCode.objects.filter(qr_type=QRCode.QRType.DYNAMIC).count(), 2)
        self.assertEqual(mock_generate.call_count, 2)

    def test_public_certificate_verification_shows_valid_status(self):
        certificate = make_certificate(self.user, certificate_id="CERT-VALID-1")

        resp = self.client.get(reverse("certificate_verify", kwargs={"code": certificate.verification_code}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CERT-VALID-1")
        self.assertContains(resp, "Aarav Sharma")
        self.assertContains(resp, "Valid")

    def test_public_certificate_verification_shows_expired_status(self):
        certificate = make_certificate(
            self.user,
            certificate_id="CERT-OLD-1",
            expiry_date=timezone.localdate() - timedelta(days=1),
        )

        resp = self.client.get(reverse("certificate_verify", kwargs={"code": certificate.verification_code}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Expired")
        self.assertContains(resp, "This certificate has expired")

    def test_revoke_certificate_updates_public_warning_and_pauses_qr(self):
        qr = make_qrcode(self.user, qr_type=QRCode.QRType.DYNAMIC, destination_url="https://example.com/verify")
        certificate = make_certificate(self.user, certificate_id="CERT-REVOKE-1", qrcode=qr)

        resp = self.client.post(reverse("qrcodes:certificate_revoke", kwargs={"pk": certificate.pk}))

        self.assertRedirects(resp, reverse("qrcodes:certificate_detail", kwargs={"pk": certificate.pk}))
        certificate.refresh_from_db()
        qr.refresh_from_db()
        self.assertEqual(certificate.status, Certificate.Status.REVOKED)
        self.assertEqual(qr.status, QRCode.Status.PAUSED)

        public = self.client.get(reverse("certificate_verify", kwargs={"code": certificate.verification_code}))
        self.assertContains(public, "Revoked")
        self.assertContains(public, "Do not trust this certificate")

    def test_certificate_detail_rejects_other_workspace(self):
        other = make_active_user(email="cert-other@example.com")
        certificate = make_certificate(other)

        resp = self.client.get(reverse("qrcodes:certificate_detail", kwargs={"pk": certificate.pk}))

        self.assertEqual(resp.status_code, 404)

    def test_template_gallery_certificate_shortcut_opens_certificate_form(self):
        resp = self.client.get(reverse("qrcodes:template_create", args=["certificate-verification"]))

        self.assertRedirects(resp, reverse("qrcodes:certificate_new"))


class QRScannerSafetyViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user(email="scanner-safety@example.com")
        self.client.force_login(self.user)

    @patch("apps.qrcodes.views.analyze_scanned_value")
    def test_safety_check_returns_url_preview(self, mock_analyze):
        mock_analyze.return_value = {
            "kind": "url",
            "normalized_url": "https://example.com",
            "host": "example.com",
            "risk_level": "safe",
            "summary": "URL preview ready.",
            "warnings": [],
            "redirect_chain": [{"host": "example.com", "url": "https://example.com"}],
            "can_open": True,
        }

        resp = self.client.post(
            reverse("qrcodes:safety_check"),
            data='{"value":"https://example.com"}',
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["host"], "example.com")
        self.assertEqual(resp.json()["risk_level"], "safe")
        mock_analyze.assert_called_once_with("https://example.com")

    def test_safety_check_requires_value(self):
        resp = self.client.post(
            reverse("qrcodes:safety_check"),
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    @patch("apps.qrcodes.views.analyze_scanner_content")
    def test_report_suspicious_creates_report(self, mock_analyze):
        mock_analyze.return_value = {
            "type": "url",
            "rawContent": "http://example.com/login.apk",
            "normalizedUrl": "http://example.com/login.apk",
            "finalUrl": "http://example.com/login.apk",
            "domain": "example.com",
            "riskLevel": "risky",
            "warnings": ["This link points to a downloadable file type commonly abused for malware."],
        }
        resp = self.client.post(
            reverse("qrcodes:report_suspicious"),
            data='{"value":"http://example.com/login.apk","reason":"malware","comment":"Looks unsafe"}',
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        report = QRSuspiciousReport.objects.get()
        self.assertEqual(report.reporter, self.user)
        self.assertEqual(report.host, "example.com")
        self.assertIn("login.apk", report.scanned_value)
        self.assertEqual(report.reason, QRSuspiciousReport.Reason.MALWARE)
        self.assertEqual(report.comment, "Looks unsafe")
        self.assertEqual(report.risk_level, "risky")
        mock_analyze.assert_called_once_with("http://example.com/login.apk", fetch_title=False)

    def test_report_suspicious_requires_value(self):
        resp = self.client.post(
            reverse("qrcodes:report_suspicious"),
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_safety_analyzer_blocks_private_network_urls(self):
        analysis = analyze_scanned_value("http://127.0.0.1/admin", follow_redirects=False)

        self.assertEqual(analysis["kind"], "url")
        self.assertEqual(analysis["risk_level"], "danger")
        self.assertFalse(analysis["can_open"])
        self.assertIn("Private/internal network destination blocked.", analysis["warnings"])

    def test_safety_analyzer_warns_on_risky_downloads(self):
        analysis = analyze_scanned_value("http://example.com/install.apk", follow_redirects=False)

        self.assertEqual(analysis["risk_level"], "warning")
        self.assertTrue(analysis["can_open"])
        self.assertIn(
            "This link points to a downloadable file type commonly abused for malware.",
            analysis["warnings"],
        )

    def test_public_scanner_pages_render(self):
        scanner = self.client.get(reverse("safe_scanner"))
        self.assertEqual(scanner.status_code, 200)
        self.assertContains(scanner, "Scan first. Preview before opening.")
        self.assertContains(scanner, "Upload QR image")
        self.assertContains(scanner, "Flashlight")

        result = self.client.get(reverse("safe_scanner_result"))
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Safety preview")
        self.assertContains(result, "Open this link anyway?")

        history = self.client.get(reverse("safe_scanner_history"))
        self.assertEqual(history.status_code, 200)
        self.assertContains(history, "Scan history")

    def test_scanner_analyze_api_requires_content(self):
        resp = self.client.post(
            reverse("scanner_analyze_api"),
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    @patch("apps.qrcodes.views.analyze_scanner_content")
    def test_scanner_analyze_api_returns_schema_and_stores_history(self, mock_analyze):
        mock_analyze.return_value = {
            "type": "url",
            "rawContent": "https://example.com",
            "domain": "example.com",
            "finalUrl": "https://example.com",
            "pageTitle": "Example",
            "isHttps": True,
            "redirectCount": 0,
            "redirectChain": [],
            "usesUrlShortener": False,
            "riskScore": 0,
            "riskLevel": "safe",
            "warnings": [],
            "recommendation": "Looks acceptable. Confirm the domain before opening.",
        }

        resp = self.client.post(
            reverse("scanner_analyze_api"),
            data='{"content":"https://example.com"}',
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["type"], "url")
        self.assertEqual(data["rawContent"], "https://example.com")
        self.assertIn("riskScore", data)
        history = QRScannerHistory.objects.get()
        self.assertEqual(history.user, self.user)
        self.assertEqual(history.raw_content, "https://example.com")
        self.assertEqual(history.content_type, "url")
        self.assertEqual(history.domain, "example.com")
        self.assertEqual(history.risk_level, "safe")

    @patch("apps.qrcodes.views.analyze_scanner_content")
    def test_scanner_analyze_api_does_not_store_history_for_anonymous(self, mock_analyze):
        self.client.logout()
        mock_analyze.return_value = {
            "type": "text",
            "rawContent": "hello",
            "domain": "",
            "finalUrl": "",
            "pageTitle": "",
            "isHttps": False,
            "redirectCount": 0,
            "redirectChain": [],
            "usesUrlShortener": False,
            "riskScore": 0,
            "riskLevel": "safe",
            "warnings": [],
            "recommendation": "No obvious risk detected. Review the content before using it.",
        }

        resp = self.client.post(
            reverse("scanner_analyze_api"),
            data='{"content":"hello"}',
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(QRScannerHistory.objects.exists())

    @patch("apps.qrcodes.views.analyze_scanner_content")
    def test_scanner_analyze_api_includes_verified_business_for_platform_qr(self, mock_analyze):
        qr = make_qrcode(self.user, short_code="verified1")
        mock_analyze.return_value = {
            "type": "url",
            "rawContent": qr.redirect_url,
            "domain": "127.0.0.1",
            "finalUrl": "",
            "pageTitle": "",
            "isHttps": False,
            "redirectCount": 0,
            "redirectChain": [],
            "usesUrlShortener": False,
            "riskScore": 60,
            "riskLevel": "risky",
            "warnings": ["Preview only."],
            "recommendation": "Review before opening.",
        }
        verified_at = timezone.now()
        BusinessVerification.objects.create(
            workspace=self.user,
            business_name="Acme Foods",
            domain="acme.example",
            method=BusinessVerification.Method.DNS,
            status=BusinessVerification.Status.VERIFIED,
            verified_at=verified_at,
        )

        resp = self.client.post(
            reverse("scanner_analyze_api"),
            data=f'{{"content":"{qr.redirect_url}"}}',
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        badge = resp.json()["verifiedBusiness"]
        self.assertTrue(badge["verified"])
        self.assertEqual(badge["badgeText"], "Verified by Narvyn")
        self.assertEqual(badge["businessName"], "Acme Foods")
        self.assertEqual(badge["domain"], "acme.example")

    @patch("apps.qrcodes.views.analyze_scanner_content")
    def test_scanner_report_api_allows_anonymous_and_links_qr(self, mock_analyze):
        cache.clear()
        campaign = QRCodeCampaign.objects.create(user=self.user, name="Menu Campaign")
        qr = make_qrcode(self.user, short_code="reportme", campaign=campaign)
        mock_analyze.return_value = {
            "type": "url",
            "rawContent": qr.redirect_url,
            "normalizedUrl": qr.redirect_url,
            "finalUrl": qr.redirect_url,
            "domain": "127.0.0.1",
            "riskLevel": "caution",
            "warnings": [],
        }
        self.client.logout()

        resp = self.client.post(
            reverse("scanner_report_api"),
            data=f'{{"content":"{qr.redirect_url}","reason":"phishing","comment":"Fake login"}}',
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        report = QRSuspiciousReport.objects.get(qrcode=qr)
        self.assertIsNone(report.reporter)
        self.assertEqual(report.campaign, campaign)
        self.assertEqual(report.reason, QRSuspiciousReport.Reason.PHISHING)
        self.assertEqual(report.comment, "Fake login")
        campaign.refresh_from_db()
        self.assertEqual(campaign.suspicious_report_count, 1)
        self.assertEqual(campaign.risk_status, QRCodeCampaign.RiskStatus.WATCH)

    @patch("apps.qrcodes.views.analyze_scanner_content")
    def test_scanner_report_api_rate_limits_anonymous_reports(self, mock_analyze):
        cache.clear()
        mock_analyze.return_value = {
            "type": "url",
            "rawContent": "https://example.com",
            "normalizedUrl": "https://example.com",
            "finalUrl": "https://example.com",
            "domain": "example.com",
            "riskLevel": "caution",
            "warnings": [],
        }
        self.client.logout()

        statuses = [
            self.client.post(
                reverse("scanner_report_api"),
                data='{"content":"https://example.com","reason":"phishing"}',
                content_type="application/json",
                REMOTE_ADDR="203.0.113.25",
            ).status_code
            for _ in range(6)
        ]

        self.assertEqual(statuses[:5], [200, 200, 200, 200, 200])
        self.assertEqual(statuses[5], 429)

    def test_repeated_open_reports_escalate_campaign_risk(self):
        campaign = QRCodeCampaign.objects.create(user=self.user, name="Risk Campaign")
        qr = make_qrcode(self.user, campaign=campaign)
        for _ in range(5):
            QRSuspiciousReport.objects.create(
                qrcode=qr,
                campaign=campaign,
                scanned_value=qr.redirect_url,
                reported_url=qr.redirect_url,
                reason=QRSuspiciousReport.Reason.PHISHING,
                risk_level="risky",
            )
        from apps.qrcodes.views import _refresh_campaign_risk

        _refresh_campaign_risk(campaign)

        campaign.refresh_from_db()
        self.assertEqual(campaign.suspicious_report_count, 5)
        self.assertEqual(campaign.risk_status, QRCodeCampaign.RiskStatus.ELEVATED)

    def test_admin_reports_requires_staff_and_renders_report(self):
        qr = make_qrcode(self.user)
        QRSuspiciousReport.objects.create(
            qrcode=qr,
            scanned_value=qr.redirect_url,
            reported_url=qr.redirect_url,
            reason=QRSuspiciousReport.Reason.SPAM,
            risk_level="caution",
        )
        resp = self.client.get(reverse("admin_reports"))
        self.assertEqual(resp.status_code, 302)

        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.client.force_login(self.user)
        resp = self.client.get(reverse("admin_reports"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Suspicious QR reports")
        self.assertContains(resp, "Spam")

    def test_admin_report_detail_updates_status_and_notes(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        report = QRSuspiciousReport.objects.create(
            scanned_value="https://example.com",
            reported_url="https://example.com",
            reason=QRSuspiciousReport.Reason.OTHER,
            risk_level="safe",
        )
        resp = self.client.post(
            reverse("admin_report_detail", args=[report.id]),
            {"status": QRSuspiciousReport.Status.RESOLVED, "admin_notes": "Reviewed"},
        )
        self.assertRedirects(resp, reverse("admin_report_detail", args=[report.id]))
        report.refresh_from_db()
        self.assertEqual(report.status, QRSuspiciousReport.Status.RESOLVED)
        self.assertEqual(report.admin_notes, "Reviewed")

    def test_scanner_analyzer_blocks_private_network_urls(self):
        analysis = analyze_scanner_content("http://127.0.0.1/admin")

        self.assertEqual(analysis["type"], "url")
        self.assertEqual(analysis["riskLevel"], "risky")
        self.assertGreaterEqual(analysis["riskScore"], 60)
        self.assertLessEqual(analysis["safetyScore"], 40)
        self.assertIn("Private/internal network destination blocked.", analysis["warnings"])

    def test_scanner_analyzer_warns_on_upi_missing_payee_name(self):
        analysis = analyze_scanner_content("upi://pay?pa=merchant@upi&am=50")

        self.assertEqual(analysis["type"], "upi")
        self.assertEqual(analysis["riskLevel"], "caution")
        self.assertIn("UPI payee name is missing.", analysis["warnings"])

    @patch("apps.qrcodes.views.analyze_url")
    def test_url_analyze_api_returns_redirect_schema(self, mock_analyze):
        mock_analyze.return_value = {
            "inputUrl": "https://short.example",
            "normalizedUrl": "https://short.example",
            "finalUrl": "https://final.example/login",
            "domain": "short.example",
            "finalDomain": "final.example",
            "redirectChain": [
                {"url": "https://short.example", "statusCode": 301, "domain": "short.example"},
                {"url": "https://final.example/login", "statusCode": 200, "domain": "final.example"},
            ],
            "redirectCount": 1,
            "isHttps": True,
            "usesUrlShortener": False,
            "hasSuspiciousKeywords": True,
            "hasExecutableDownload": False,
            "riskLevel": "caution",
            "riskScore": 55,
            "warnings": ["The original domain and final domain do not match."],
        }

        resp = self.client.post(
            reverse("url_analyze_api"),
            data='{"url":"https://short.example"}',
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["finalDomain"], "final.example")
        self.assertEqual(data["redirectChain"][0]["statusCode"], 301)
        mock_analyze.assert_called_once_with("https://short.example")

    def test_url_analyze_api_requires_url(self):
        resp = self.client.post(
            reverse("url_analyze_api"),
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_url_analyzer_blocks_private_network_without_request(self):
        analysis = analyze_url("http://127.0.0.1/admin")

        self.assertEqual(analysis["riskLevel"], "risky")
        self.assertEqual(analysis["safetyScore"], 0)
        self.assertEqual(analysis["redirectCount"], 0)
        self.assertIn("Blocked localhost, private IP, or internal network destination.", analysis["warnings"])

    @patch("apps.qrcodes.safety._safe_redirect_chain")
    def test_url_analyzer_detects_keywords_executable_and_punycode(self, mock_chain):
        mock_chain.return_value = {
            "finalUrl": "https://xn--paytm-9ve.example/update-password.apk",
            "pageTitle": "",
            "redirectChain": [
                {
                    "url": "https://xn--paytm-9ve.example/update-password.apk",
                    "statusCode": 200,
                    "domain": "xn--paytm-9ve.example",
                }
            ],
            "redirectCount": 0,
            "warning": "",
        }

        analysis = analyze_url("https://xn--paytm-9ve.example/update-password.apk")

        self.assertEqual(analysis["riskLevel"], "risky")
        self.assertLess(analysis["safetyScore"], 50)
        self.assertTrue(analysis["hasSuspiciousKeywords"])
        self.assertTrue(analysis["hasExecutableDownload"])
        self.assertTrue(any("punycode" in warning.lower() for warning in analysis["warnings"]))

    def test_free_plan_blocks_qr_creation_after_limit(self):
        for idx in range(5):
            make_qrcode(self.user, name=f"Limit QR {idx}")

        resp = self.client.get(reverse("qrcodes:create"))

        self.assertRedirects(resp, reverse("core:pricing"))

    def test_expired_paid_plan_blocks_creation_at_free_limit(self):
        self.user.plan = "pro"
        self.user.role = User.Role.PRO
        self.user.plan_expires_at = timezone.now() - timedelta(days=1)
        self.user.save(update_fields=["plan", "role", "plan_expires_at"])
        for idx in range(5):
            make_qrcode(self.user, name=f"Expired plan QR {idx}")

        resp = self.client.post(reverse("qrcodes:create"), {
            **QRCodeFormTest.BASE_FORM_DATA,
            "name": "Blocked QR",
            "qr_type": "url",
            "content": "https://example.com",
        })

        self.assertRedirects(resp, reverse("core:pricing"))

    def test_template_gallery_renders_categories_and_templates(self):
        resp = self.client.get(reverse("qrcodes:templates"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Template Gallery")
        self.assertContains(resp, "Restaurant")
        self.assertContains(resp, "UPI payment")
        self.assertContains(resp, "Use template")

    @patch("apps.qrcodes.views._generate_qr_now", return_value=True)
    def test_website_template_creates_dynamic_qr_and_campaign(self, mock_generate):
        resp = self.client.post(reverse("qrcodes:template_create", args=["website-url"]), {
            "name": "Main website",
            "campaign_name": "Website launch",
            "url": "example.com",
        })

        qr = QRCode.objects.get(name="Main website")
        self.assertRedirects(resp, reverse("qrcodes:detail", args=[qr.id]))
        self.assertEqual(qr.qr_type, QRCode.QRType.DYNAMIC)
        self.assertEqual(qr.destination_url, "https://example.com")
        self.assertEqual(qr.content, "https://example.com")
        self.assertEqual(qr.campaign.name, "Website launch")
        self.assertIn("website-url", qr.tags)
        mock_generate.assert_called_once_with(qr)

    def test_template_create_validates_relevant_fields(self):
        resp = self.client.post(reverse("qrcodes:template_create", args=["website-url"]), {
            "name": "Broken website",
        })

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Website URL is required.")
        self.assertFalse(QRCode.objects.filter(name="Broken website").exists())

    @patch("apps.qrcodes.views._generate_qr_now", return_value=True)
    def test_upi_template_builds_payment_payload(self, mock_generate):
        resp = self.client.post(reverse("qrcodes:template_create", args=["upi-payment"]), {
            "name": "Counter payment",
            "payee_vpa": "merchant@upi",
            "payee_name": "Narvyn Cafe",
            "amount": "149.50",
            "note": "Table order",
        })

        qr = QRCode.objects.get(name="Counter payment")
        self.assertRedirects(resp, reverse("qrcodes:detail", args=[qr.id]))
        self.assertEqual(qr.qr_type, QRCode.QRType.TEXT)
        self.assertIn("upi://pay?", qr.content)
        self.assertIn("pa=merchant%40upi", qr.content)
        self.assertIn("am=149.50", qr.content)
        self.assertIsNotNone(qr.campaign)
        mock_generate.assert_called_once_with(qr)

    def test_paid_template_requires_plan(self):
        resp = self.client.get(reverse("qrcodes:template_create", args=["invoice-payment"]))

        self.assertRedirects(resp, reverse("core:pricing"))
        self.assertFalse(QRCode.objects.filter(user=self.user, name="Blocked QR").exists())


@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class QRCodeListViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()
        self.client.force_login(self.user)

    def test_list_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("qrcodes:list"))
        self.assertEqual(resp.status_code, 302)

    def test_list_renders_empty(self):
        resp = self.client.get(reverse("qrcodes:list"))
        self.assertEqual(resp.status_code, 200)

    def test_list_shows_user_qrcodes(self):
        make_qrcode(self.user, name="My QR")
        other = make_active_user(email="other@example.com")
        make_qrcode(other, name="Other QR")
        resp = self.client.get(reverse("qrcodes:list"))
        qrcodes = resp.context["qrcodes"]
        names = [qr.name for qr in qrcodes]
        self.assertIn("My QR", names)
        self.assertNotIn("Other QR", names)

    def test_list_search_filter(self):
        make_qrcode(self.user, name="Campaign Alpha")
        make_qrcode(self.user, name="Campaign Beta")
        resp = self.client.get(reverse("qrcodes:list") + "?q=Alpha")
        qrcodes = list(resp.context["qrcodes"])
        self.assertEqual(len(qrcodes), 1)
        self.assertEqual(qrcodes[0].name, "Campaign Alpha")

    def test_list_excludes_deleted(self):
        make_qrcode(self.user, name="Deleted QR", status=QRCode.Status.DELETED)
        resp = self.client.get(reverse("qrcodes:list"))
        names = [qr.name for qr in resp.context["qrcodes"]]
        self.assertNotIn("Deleted QR", names)


@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class QRCodeCreateViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()
        self.user.plan = "pro"
        self.user.role = User.Role.PRO
        self.user.save(update_fields=["plan", "role"])
        self.client.force_login(self.user)

    def test_get_renders_form(self):
        resp = self.client.get(reverse("qrcodes:create"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("form", resp.context)
        self.assertContains(resp, "<details class=\"disclosure-card group\"", count=4)
        self.assertContains(resp, "Ready with the basic details?")
        self.assertContains(resp, "Optional colors, logo, frame, shape, and output size.")

    @patch("apps.qrcodes.views._generate_qr_now", return_value=True)
    def test_post_valid_creates_qr(self, mock_generate):
        resp = self.client.post(reverse("qrcodes:create"), {
            "name": "New URL QR",
            "qr_type": "url",
            "content": "https://example.com",
            "foreground_color": "#000000",
            "background_color": "#FFFFFF",
            "dot_style": "square",
            "corner_style": "square",
            "outer_shape": "circle",
            "error_correction": "M",
            "qr_size": 300,
            "logo_size_ratio": 0.2,
            "frame_color": "#000000",
            "tags": "[]",
        })
        self.assertEqual(QRCode.objects.filter(user=self.user, name="New URL QR").count(), 1)
        self.assertEqual(QRCode.objects.get(user=self.user, name="New URL QR").outer_shape, "circle")
        self.assertEqual(resp.status_code, 302)
        mock_generate.assert_called_once()

    @patch("apps.qrcodes.views._generate_qr_now")
    def test_post_invalid_rerenders(self, mock_generate):
        resp = self.client.post(reverse("qrcodes:create"), {
            "name": "",
            "qr_type": "url",
            "content": "",
        })
        self.assertEqual(resp.status_code, 200)
        mock_generate.assert_not_called()


@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class QRCodeDetailViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()
        self.user.plan = "pro"
        self.user.role = User.Role.PRO
        self.user.save(update_fields=["plan", "role"])
        self.client.force_login(self.user)

    def test_detail_renders(self):
        qr = make_qrcode(self.user)
        resp = self.client.get(reverse("qrcodes:detail", args=[qr.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["qr"], qr)

    def test_detail_forbidden_for_other_user(self):
        other = make_active_user(email="other2@example.com")
        qr = make_qrcode(other)
        resp = self.client.get(reverse("qrcodes:detail", args=[qr.id]))
        self.assertEqual(resp.status_code, 404)


@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class QRCodeEditViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()
        self.user.plan = "pro"
        self.user.role = User.Role.PRO
        self.user.save(update_fields=["plan", "role"])
        self.client.force_login(self.user)

    def test_get_renders_form(self):
        qr = make_qrcode(self.user)
        resp = self.client.get(reverse("qrcodes:edit", args=[qr.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("form", resp.context)

    @patch("apps.qrcodes.views._generate_qr_now", return_value=True)
    def test_post_updates_qr(self, mock_generate):
        qr = make_qrcode(self.user)
        resp = self.client.post(reverse("qrcodes:edit", args=[qr.id]), {
            "name": "Updated Name",
            "qr_type": "url",
            "content": "https://updated.com",
            "foreground_color": "#000000",
            "background_color": "#FFFFFF",
            "dot_style": "square",
            "corner_style": "square",
            "outer_shape": "duck",
            "error_correction": "M",
            "qr_size": 300,
            "logo_size_ratio": 0.2,
            "frame_color": "#000000",
            "tags": "[]",
        })
        self.assertEqual(resp.status_code, 302)
        qr.refresh_from_db()
        self.assertEqual(qr.name, "Updated Name")
        self.assertEqual(qr.outer_shape, "duck")
        mock_generate.assert_called_once()

    def test_deleted_qr_raises_404(self):
        qr = make_qrcode(self.user, status=QRCode.Status.DELETED)
        resp = self.client.get(reverse("qrcodes:edit", args=[qr.id]))
        self.assertEqual(resp.status_code, 404)


@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class QRCodeDeleteViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()
        self.client.force_login(self.user)

    def test_delete_soft_deletes(self):
        qr = make_qrcode(self.user)
        resp = self.client.post(reverse("qrcodes:delete", args=[qr.id]))
        self.assertEqual(resp.status_code, 302)
        qr.refresh_from_db()
        self.assertEqual(qr.status, QRCode.Status.DELETED)

    def test_delete_requires_post(self):
        qr = make_qrcode(self.user)
        resp = self.client.get(reverse("qrcodes:delete", args=[qr.id]))
        self.assertEqual(resp.status_code, 405)

    def test_delete_forbidden_for_other_user(self):
        other = make_active_user(email="other3@example.com")
        qr = make_qrcode(other)
        resp = self.client.post(reverse("qrcodes:delete", args=[qr.id]))
        self.assertEqual(resp.status_code, 404)


@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class QRCodeDownloadViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()
        self.client.force_login(self.user)

    def test_download_png_generates_when_missing(self):
        qr = make_qrcode(self.user)
        resp = self.client.get(reverse("qrcodes:download", args=[qr.id, "png"]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        qr.refresh_from_db()
        self.assertTrue(qr.image_png)

    def test_download_svg_generates_when_missing(self):
        qr = make_qrcode(self.user)
        resp = self.client.get(reverse("qrcodes:download", args=[qr.id, "svg"]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/svg+xml")
        qr.refresh_from_db()
        self.assertTrue(qr.image_svg)
        self.assertIn("<svg", qr.image_svg)

    def test_shaped_qr_generates_downloads(self):
        qr = make_qrcode(self.user, outer_shape=QRCode.OuterShape.DUCK)
        resp = self.client.get(reverse("qrcodes:download", args=[qr.id, "png"]))
        self.assertEqual(resp.status_code, 200)
        qr.refresh_from_db()
        self.assertTrue(qr.image_png)
        self.assertTrue(qr.image_svg)

    def test_download_invalid_format(self):
        qr = make_qrcode(self.user)
        resp = self.client.get(reverse("qrcodes:download", args=[qr.id, "exe"]))
        self.assertEqual(resp.status_code, 400)


@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class QRRedirectViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()

    @patch("apps.qrcodes.views.process_scan_event")
    def test_redirect_to_content(self, mock_task):
        mock_task.delay = MagicMock()
        qr = make_qrcode(self.user, content="https://destination.com")
        resp = self.client.get(f"/r/{qr.short_code}/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://destination.com")

    @patch("apps.qrcodes.views.process_scan_event")
    def test_expired_returns_410(self, mock_task):
        mock_task.delay = MagicMock()
        past = timezone.now() - timedelta(hours=1)
        qr = make_qrcode(self.user, expires_at=past)
        resp = self.client.get(f"/r/{qr.short_code}/")
        self.assertEqual(resp.status_code, 410)

    @patch("apps.qrcodes.views.process_scan_event")
    def test_paused_returns_200(self, mock_task):
        mock_task.delay = MagicMock()
        qr = make_qrcode(self.user, status=QRCode.Status.PAUSED, content="https://example.com")
        resp = self.client.get(f"/r/{qr.short_code}/")
        self.assertEqual(resp.status_code, 200)

    def test_unknown_short_code_returns_404(self):
        resp = self.client.get("/r/doesnotexist123/")
        self.assertEqual(resp.status_code, 404)

    @patch("apps.qrcodes.views.process_scan_event")
    def test_password_protected_shows_gate(self, mock_task):
        mock_task.delay = MagicMock()
        qr = make_qrcode(self.user, is_password_protected=True, content="https://example.com")
        qr.set_access_password("mysecret")
        qr.save()
        resp = self.client.get(f"/r/{qr.short_code}/")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "qrcodes/password_gate.html")

    @patch("apps.qrcodes.views.process_scan_event")
    def test_password_gate_wrong_password(self, mock_task):
        mock_task.delay = MagicMock()
        qr = make_qrcode(self.user, is_password_protected=True, content="https://example.com")
        qr.set_access_password("correct")
        qr.save()
        resp = self.client.post(f"/r/{qr.short_code}/", {"password": "wrong"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Incorrect password")

    @patch("apps.qrcodes.views.process_scan_event")
    def test_dynamic_redirect_uses_destination_url(self, mock_task):
        mock_task.delay = MagicMock()
        qr = make_qrcode(
            self.user,
            qr_type=QRCode.QRType.DYNAMIC,
            content="https://dest.com",
            destination_url="https://dest.com",
        )
        resp = self.client.get(f"/r/{qr.short_code}/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://dest.com")

    @patch("apps.qrcodes.views.process_scan_event")
    def test_empty_destination_returns_410(self, mock_task):
        mock_task.delay = MagicMock()
        qr = make_qrcode(
            self.user,
            qr_type=QRCode.QRType.DYNAMIC,
            content="",
            destination_url="",
        )
        resp = self.client.get(f"/r/{qr.short_code}/")
        self.assertEqual(resp.status_code, 410)

    def test_redirect_updates_scan_count_without_celery_worker(self):
        qr = make_qrcode(self.user, content="https://destination.com")
        resp = self.client.get(
            f"/r/{qr.short_code}/",
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
            REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual(resp.status_code, 302)
        qr.refresh_from_db()
        self.assertEqual(qr.total_scans, 1)
        self.assertEqual(qr.unique_scans, 1)

    def test_vcard_redirect_counts_and_serves_contact(self):
        qr = make_qrcode(
            self.user,
            qr_type=QRCode.QRType.VCARD,
            content="BEGIN:VCARD\nVERSION:3.0\nFN:Test Person\nEND:VCARD",
        )
        resp = self.client.get(
            f"/r/{qr.short_code}/",
            HTTP_USER_AGENT="Mozilla/5.0",
            REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/vcard", resp["Content-Type"])
        self.assertContains(resp, "BEGIN:VCARD")
        qr.refresh_from_db()
        self.assertEqual(qr.total_scans, 1)
        self.assertEqual(qr.unique_scans, 1)

    def test_text_redirect_counts_and_serves_content_page(self):
        qr = make_qrcode(self.user, qr_type=QRCode.QRType.TEXT, content="Tracked note")
        resp = self.client.get(
            f"/r/{qr.short_code}/",
            HTTP_USER_AGENT="Mozilla/5.0",
            REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "qrcodes/tracked_content.html")
        self.assertContains(resp, "Tracked note")
        qr.refresh_from_db()
        self.assertEqual(qr.total_scans, 1)

    def test_email_redirect_counts_and_serves_action_page(self):
        qr = make_qrcode(self.user, qr_type=QRCode.QRType.EMAIL, content="mailto:test@example.com")
        resp = self.client.get(
            f"/r/{qr.short_code}/",
            HTTP_USER_AGENT="Mozilla/5.0",
            REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "qrcodes/tracked_action.html")
        self.assertContains(resp, "Open email app")
        qr.refresh_from_db()
        self.assertEqual(qr.total_scans, 1)

    def test_smart_destination_device_rule_redirects_and_counts_hits(self):
        qr = make_qrcode(self.user, content="https://default.example")
        rule = QRDestinationRule.objects.create(
            qrcode=qr,
            name="Mobile route",
            rule_type=QRDestinationRule.RuleType.DEVICE,
            match_value="mobile",
            destination_url="https://m.example",
        )
        resp = self.client.get(
            f"/r/{qr.short_code}/",
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://m.example")
        rule.refresh_from_db()
        self.assertEqual(rule.hits, 1)


@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class QRPremiumFeatureTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user(email="premium@example.com")
        self.user.plan = "pro"
        self.user.role = User.Role.PRO
        self.user.save(update_fields=["plan", "role"])
        self.client.force_login(self.user)

    def test_scan_page_renders_phone_scanner_fallbacks(self):
        resp = self.client.get(reverse("qrcodes:scan"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "jsQR")
        self.assertContains(resp, "Recent scans")
        self.assertContains(resp, "capture=\"environment\"")
        self.assertContains(resp, "Open result")
        self.assertContains(resp, "showResult")
        self.assertContains(resp, "startCamera({ auto: true })")

    def test_premium_studio_renders(self):
        make_qrcode(self.user, name="Studio QR")
        resp = self.client.get(reverse("qrcodes:premium"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Smart destinations")
        self.assertContains(resp, "Studio QR")

    @patch("apps.qrcodes.premium.requests.head")
    def test_preflight_health_check_saves_result(self, mock_head):
        qr = make_qrcode(self.user, content="https://healthy.example")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://healthy.example/"
        mock_head.return_value = mock_response

        resp = self.client.post(reverse("qrcodes:preflight", args=[qr.id]), {"action": "health"})
        self.assertEqual(resp.status_code, 302)
        qr.refresh_from_db()
        self.assertEqual(qr.health_check.status, "ok")
        self.assertEqual(qr.health_check.status_code, 200)

    def test_landing_page_and_conversion_tracking(self):
        qr = make_qrcode(self.user, content="https://default.example")
        QRLandingPage.objects.create(
            qrcode=qr,
            title="Menu",
            body="Fresh menu",
            primary_label="Open menu",
            primary_url="https://restaurant.example/menu",
        )
        resp = self.client.get(f"/p/{qr.short_code}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Open menu")

        resp = self.client.get(f"/c/{qr.short_code}/review/?next=https%3A%2F%2Freviews.example")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://reviews.example")
        self.assertTrue(QRConversionEvent.objects.filter(qrcode=qr, event_type="review").exists())
