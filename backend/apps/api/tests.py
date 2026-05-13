"""
Comprehensive tests for the REST API (v1).
Covers: JWT auth, QRCode viewset, Barcode viewset, Analytics viewset, User viewset.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.qrcodes.models import QRCode, QRCodeCampaign, QRScanEvent
from apps.barcodes.models import Barcode
from apps.analytics.models import DailyQRStats

User = get_user_model()

CACHE_OVERRIDE = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}


def make_active_user(email="api@example.com", password="testpass123", **kw):
    user = User.objects.create_user(
        email=email, password=password,
        full_name=kw.pop("full_name", "API Tester"), **kw,
    )
    user.is_active = True
    user.is_email_verified = True
    user.save(update_fields=["is_active", "is_email_verified"])
    return user


def make_qrcode(user, **kw):
    defaults = {
        "name": "API Test QR",
        "qr_type": QRCode.QRType.URL,
        "content": "https://example.com",
    }
    defaults.update(kw)
    return QRCode.objects.create(user=user, **defaults)


def make_barcode(user, **kw):
    defaults = {
        "name": "API Test Barcode",
        "barcode_format": Barcode.BarcodeFormat.CODE128,
        "content": "ABC123",
    }
    defaults.update(kw)
    return Barcode.objects.create(user=user, **defaults)


# ── JWT auth tests ────────────────────────────────────────────────────────────

@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class JWTAuthTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_active_user(password="StrongPass123!")

    def test_obtain_token(self):
        resp = self.client.post(reverse("api_v1:token_obtain_pair"), {
            "email": "api@example.com",
            "password": "StrongPass123!",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

    def test_wrong_credentials_rejected(self):
        resp = self.client.post(reverse("api_v1:token_obtain_pair"), {
            "email": "api@example.com",
            "password": "wrongpassword",
        })
        self.assertEqual(resp.status_code, 401)

    def test_unauthenticated_request_rejected(self):
        resp = self.client.get("/api/v1/qrcodes/")
        self.assertEqual(resp.status_code, 401)

    def test_authenticated_request_allowed(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/api/v1/qrcodes/")
        self.assertEqual(resp.status_code, 200)

    def test_token_refresh(self):
        token_resp = self.client.post(reverse("api_v1:token_obtain_pair"), {
            "email": "api@example.com",
            "password": "StrongPass123!",
        })
        refresh = token_resp.data["refresh"]
        resp = self.client.post(reverse("api_v1:token_refresh"), {"refresh": refresh})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)


# ── QRCode ViewSet tests ──────────────────────────────────────────────────────

@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class QRCodeViewSetTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_active_user()
        self.client.force_authenticate(user=self.user)

    def test_list_empty(self):
        resp = self.client.get("/api/v1/qrcodes/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 0)

    def test_list_returns_user_qrcodes(self):
        make_qrcode(self.user, name="QR A")
        make_qrcode(self.user, name="QR B")
        other = make_active_user(email="other@api.com")
        make_qrcode(other, name="QR C")
        resp = self.client.get("/api/v1/qrcodes/")
        self.assertEqual(resp.data["count"], 2)

    def test_list_excludes_deleted(self):
        make_qrcode(self.user, status=QRCode.Status.DELETED)
        resp = self.client.get("/api/v1/qrcodes/")
        self.assertEqual(resp.data["count"], 0)

    @patch("apps.qrcodes.tasks.generate_qr_images_task")
    def test_create_qrcode(self, mock_task):
        mock_task.delay = MagicMock()
        resp = self.client.post("/api/v1/qrcodes/", {
            "name": "API Created QR",
            "qr_type": "url",
            "content": "https://example.com",
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["name"], "API Created QR")

    def test_retrieve_qrcode(self):
        qr = make_qrcode(self.user)
        resp = self.client.get(f"/api/v1/qrcodes/{qr.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], qr.name)

    def test_retrieve_other_users_qrcode_forbidden(self):
        other = make_active_user(email="other2@api.com")
        qr = make_qrcode(other)
        resp = self.client.get(f"/api/v1/qrcodes/{qr.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_update_qrcode(self):
        qr = make_qrcode(self.user)
        resp = self.client.patch(f"/api/v1/qrcodes/{qr.id}/", {"name": "Updated via API"})
        self.assertEqual(resp.status_code, 200)
        qr.refresh_from_db()
        self.assertEqual(qr.name, "Updated via API")

    def test_delete_qrcode_soft_deletes(self):
        qr = make_qrcode(self.user)
        resp = self.client.delete(f"/api/v1/qrcodes/{qr.id}/")
        self.assertEqual(resp.status_code, 204)
        qr.refresh_from_db()
        self.assertEqual(qr.status, QRCode.Status.DELETED)

    def test_update_destination_on_dynamic(self):
        qr = make_qrcode(
            self.user, qr_type=QRCode.QRType.DYNAMIC,
            content="https://old.com", destination_url="https://old.com",
        )
        resp = self.client.patch(
            f"/api/v1/qrcodes/{qr.id}/destination/",
            {"destination_url": "https://new.com"},
        )
        self.assertEqual(resp.status_code, 200)
        qr.refresh_from_db()
        self.assertEqual(qr.destination_url, "https://new.com")

    def test_update_destination_rejects_static(self):
        qr = make_qrcode(self.user, qr_type=QRCode.QRType.URL)
        resp = self.client.patch(
            f"/api/v1/qrcodes/{qr.id}/destination/",
            {"destination_url": "https://new.com"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_filter_by_type(self):
        make_qrcode(self.user, name="URL QR", qr_type=QRCode.QRType.URL)
        make_qrcode(
            self.user, name="Text QR", qr_type=QRCode.QRType.TEXT, content="hello"
        )
        resp = self.client.get("/api/v1/qrcodes/?qr_type=url")
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["name"], "URL QR")

    def test_search_by_name(self):
        make_qrcode(self.user, name="Campaign Alpha")
        make_qrcode(self.user, name="Campaign Beta")
        resp = self.client.get("/api/v1/qrcodes/?search=Alpha")
        self.assertEqual(resp.data["count"], 1)

    def test_create_rejects_invalid_url_and_colors(self):
        resp = self.client.post("/api/v1/qrcodes/", {
            "name": "Bad API QR",
            "qr_type": "url",
            "content": "not a url",
            "foreground_color": "black",
        })

        self.assertEqual(resp.status_code, 400)
        self.assertIn("content", resp.data["detail"])

        resp = self.client.post("/api/v1/qrcodes/", {
            "name": "Invisible API QR",
            "qr_type": "url",
            "content": "https://example.com",
            "foreground_color": "#000000",
            "background_color": "#000000",
        })

        self.assertEqual(resp.status_code, 400)
        self.assertIn("background_color", resp.data["detail"])

    def test_create_rejects_unsafe_generation_sizes(self):
        resp = self.client.post("/api/v1/qrcodes/", {
            "name": "Huge API QR",
            "qr_type": "url",
            "content": "https://example.com",
            "qr_size": 4096,
            "logo_size_ratio": 0.6,
        })

        self.assertEqual(resp.status_code, 400)
        self.assertIn("qr_size", resp.data["detail"])

    def test_create_rejects_expired_plan_and_past_expiry(self):
        self.user.plan = "pro"
        self.user.role = User.Role.PRO
        self.user.plan_expires_at = timezone.now() - timedelta(days=1)
        self.user.save(update_fields=["plan", "role", "plan_expires_at"])

        resp = self.client.post("/api/v1/qrcodes/", {
            "name": "Expired Plan QR",
            "qr_type": "url",
            "content": "https://example.com",
            "outer_shape": "circle",
        })

        self.assertEqual(resp.status_code, 400)
        self.assertIn("outer_shape", resp.data["detail"])

        resp = self.client.post("/api/v1/qrcodes/", {
            "name": "Past Expiry QR",
            "qr_type": "url",
            "content": "https://example.com",
            "expires_at": (timezone.now() - timedelta(hours=1)).isoformat(),
        })

        self.assertEqual(resp.status_code, 400)
        self.assertIn("expires_at", resp.data["detail"])


# ── Barcode ViewSet tests ─────────────────────────────────────────────────────

@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class BarcodeViewSetTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_active_user(email="bc_api@example.com")
        self.client.force_authenticate(user=self.user)

    def test_list_empty(self):
        resp = self.client.get("/api/v1/barcodes/")
        self.assertEqual(resp.status_code, 200)

    def test_list_returns_user_barcodes(self):
        make_barcode(self.user)
        other = make_active_user(email="other@bc_api.com")
        make_barcode(other)
        resp = self.client.get("/api/v1/barcodes/")
        self.assertEqual(resp.data["count"], 1)

    def test_retrieve_barcode(self):
        bc = make_barcode(self.user)
        resp = self.client.get(f"/api/v1/barcodes/{bc.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], bc.name)

    def test_delete_barcode(self):
        bc = make_barcode(self.user)
        resp = self.client.delete(f"/api/v1/barcodes/{bc.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Barcode.objects.filter(id=bc.id).exists())

    @patch("apps.barcodes.utils.validate_barcode_content", return_value=(True, None))
    def test_bulk_validate(self, mock_validate):
        resp = self.client.post("/api/v1/barcodes/bulk-validate/", {
            "items": ["ABC", "DEF", "GHI"],
            "format": "code128",
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("results", resp.data)
        self.assertEqual(len(resp.data["results"]), 3)


# ── User ViewSet tests ────────────────────────────────────────────────────────

@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class UserViewSetTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_active_user(email="user_api@example.com")
        self.client.force_authenticate(user=self.user)

    def test_me_returns_current_user(self):
        resp = self.client.get("/api/v1/users/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["email"], self.user.email)

    def test_me_unauthenticated(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/v1/users/me/")
        self.assertEqual(resp.status_code, 401)

    def test_update_me_full_name(self):
        resp = self.client.patch("/api/v1/users/me/update/", {"full_name": "Updated Name"})
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Updated Name")


# ── Analytics ViewSet tests ───────────────────────────────────────────────────

@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class AnalyticsViewSetTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_active_user(email="analytics_api@example.com")
        self.client.force_authenticate(user=self.user)

    def test_summary_returns_data(self):
        resp = self.client.get("/api/v1/analytics/summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("active_qr_count", resp.data)
        self.assertIn("total_scans", resp.data)
        self.assertIn("unique_scans", resp.data)
        self.assertIn("daily_stats", resp.data)

    def test_summary_counts_processed_scan_events(self):
        qr = make_qrcode(self.user)
        QRScanEvent.objects.create(
            qrcode=qr,
            ip_address="127.0.0.1",
            user_agent_raw="Mozilla/5.0",
            fingerprint="api-scan-1",
            is_unique=True,
            is_processed=True,
            device_type="desktop",
        )
        QRScanEvent.objects.create(
            qrcode=qr,
            ip_address="127.0.0.1",
            user_agent_raw="Mozilla/5.0",
            fingerprint="api-scan-2",
            is_unique=False,
            is_processed=True,
            device_type="desktop",
        )

        resp = self.client.get("/api/v1/analytics/summary/?days=7")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["total_scans"], 2)
        self.assertEqual(resp.data["unique_scans"], 1)

    def test_summary_unauthenticated(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/v1/analytics/summary/")
        self.assertEqual(resp.status_code, 401)
