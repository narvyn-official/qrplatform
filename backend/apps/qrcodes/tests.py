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

from apps.qrcodes.models import (
    QRCode,
    QRCodeCampaign,
    QRScanEvent,
    QRDestinationRule,
    QRLandingPage,
    QRConversionEvent,
)
from apps.qrcodes.forms import QRCodeForm
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

    def test_encoded_content_static(self):
        qr = make_qrcode(self.user, content="https://example.com")
        self.assertIn(qr.short_code, qr.encoded_content)

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
        self.assertContains(resp, "Fast local QR scanning")

    def test_dashboard_renders(self):
        resp = self.client.get(reverse("qrcodes:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("quick_actions", resp.context)

    def test_dashboard_shows_recent_qrcodes(self):
        make_qrcode(self.user, name="Recent QR")
        resp = self.client.get(reverse("qrcodes:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("recent_qrcodes", resp.context)


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
        self.client.force_login(self.user)

    def test_get_renders_form(self):
        resp = self.client.get(reverse("qrcodes:create"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("form", resp.context)

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
        self.client.force_login(self.user)

    def test_scan_page_renders_phone_scanner_fallbacks(self):
        resp = self.client.get(reverse("qrcodes:scan"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "jsQR")
        self.assertContains(resp, "Recent scans")
        self.assertContains(resp, "capture=\"environment\"")

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
