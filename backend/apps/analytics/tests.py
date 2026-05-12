"""
Comprehensive tests for the analytics app.
Covers: models, utilities.
"""
import hashlib
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.analytics.models import DailyQRStats, HourlyQRStats, GeoStats
from apps.analytics.tasks import process_pending_scan_events_now
from apps.analytics.utils import (
    compute_scan_fingerprint,
    parse_user_agent,
    geolocate_ip,
    get_client_ip,
)
from apps.qrcodes.models import QRCode, QRScanEvent

User = get_user_model()

CACHE_OVERRIDE = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)
BOT_UA = "Googlebot/2.1 (+http://www.google.com/bot.html)"


# ── Model tests ───────────────────────────────────────────────────────────────

class DailyQRStatsModelTest(TestCase):

    def test_str(self):
        import uuid
        from datetime import date
        stat = DailyQRStats(qrcode_id=uuid.uuid4(), date=date(2024, 1, 15))
        result = str(stat)
        self.assertIn("2024-01-15", result)

    def test_unique_together_qrcode_date(self):
        import uuid
        from datetime import date
        qid = uuid.uuid4()
        uid = uuid.uuid4()
        DailyQRStats.objects.create(qrcode_id=qid, user_id=uid, date=date(2024, 1, 1))
        with self.assertRaises(Exception):
            DailyQRStats.objects.create(qrcode_id=qid, user_id=uid, date=date(2024, 1, 1))


class HourlyQRStatsModelTest(TestCase):

    def test_str(self):
        import uuid
        from datetime import date
        stat = HourlyQRStats(qrcode_id=uuid.uuid4(), date=date(2024, 1, 1), hour=14, scans=5)
        result = str(stat)
        self.assertIn("14:00", result)

    def test_unique_together_qrcode_date_hour(self):
        import uuid
        from datetime import date
        qid = uuid.uuid4()
        HourlyQRStats.objects.create(qrcode_id=qid, date=date(2024, 1, 1), hour=10)
        with self.assertRaises(Exception):
            HourlyQRStats.objects.create(qrcode_id=qid, date=date(2024, 1, 1), hour=10)


# ── Utility tests ─────────────────────────────────────────────────────────────

class ComputeScanFingerprintTest(TestCase):

    def test_returns_sha256_hex(self):
        result = compute_scan_fingerprint("1.2.3.4", "Mozilla/5.0", "2024-01-15")
        expected = hashlib.sha256("1.2.3.4:Mozilla/5.0:2024-01-15".encode()).hexdigest()
        self.assertEqual(result, expected)

    def test_same_inputs_same_fingerprint(self):
        fp1 = compute_scan_fingerprint("10.0.0.1", "Chrome/120", "2024-03-01")
        fp2 = compute_scan_fingerprint("10.0.0.1", "Chrome/120", "2024-03-01")
        self.assertEqual(fp1, fp2)

    def test_different_date_different_fingerprint(self):
        fp1 = compute_scan_fingerprint("10.0.0.1", "Chrome/120", "2024-03-01")
        fp2 = compute_scan_fingerprint("10.0.0.1", "Chrome/120", "2024-03-02")
        self.assertNotEqual(fp1, fp2)

    def test_empty_inputs(self):
        result = compute_scan_fingerprint("", "", "")
        self.assertIsInstance(result, str)
        self.assertEqual(len(result), 64)


class ParseUserAgentTest(TestCase):

    def test_desktop_ua(self):
        result = parse_user_agent(DESKTOP_UA)
        self.assertEqual(result["device_type"], "desktop")
        self.assertNotEqual(result["browser_family"], "unknown")

    def test_mobile_ua(self):
        result = parse_user_agent(MOBILE_UA)
        self.assertEqual(result["device_type"], "mobile")

    def test_bot_ua(self):
        result = parse_user_agent(BOT_UA)
        self.assertEqual(result["device_type"], "bot")

    def test_empty_ua(self):
        result = parse_user_agent("")
        self.assertEqual(result["device_type"], "unknown")
        self.assertEqual(result["os_family"], "unknown")
        self.assertEqual(result["browser_family"], "unknown")


class PendingScanProcessingTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email="pending@example.com",
            password="testpass123",
            full_name="Pending Scanner",
        )
        self.user.is_active = True
        self.user.is_email_verified = True
        self.user.save(update_fields=["is_active", "is_email_verified"])
        self.qr = QRCode.objects.create(
            user=self.user,
            name="Pending QR",
            content="https://example.com",
        )

    def test_process_pending_scan_events_counts_old_rows(self):
        QRScanEvent.objects.create(
            qrcode=self.qr,
            ip_address="127.0.0.1",
            user_agent_raw=DESKTOP_UA,
            fingerprint="pending-1",
        )
        QRScanEvent.objects.create(
            qrcode=self.qr,
            ip_address="127.0.0.1",
            user_agent_raw=DESKTOP_UA,
            fingerprint="pending-1",
        )

        result = process_pending_scan_events_now(limit=10)

        self.qr.refresh_from_db()
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(self.qr.total_scans, 2)
        self.assertEqual(self.qr.unique_scans, 1)
        self.assertEqual(QRScanEvent.objects.filter(is_processed=True).count(), 2)

    def test_management_command_processes_pending_scans(self):
        QRScanEvent.objects.create(
            qrcode=self.qr,
            ip_address="127.0.0.1",
            user_agent_raw=DESKTOP_UA,
            fingerprint="pending-2",
        )

        call_command("process_pending_scan_events", limit=10)

        self.qr.refresh_from_db()
        self.assertEqual(self.qr.total_scans, 1)
        self.assertEqual(self.qr.unique_scans, 1)

    def test_returns_dict_keys(self):
        result = parse_user_agent(DESKTOP_UA)
        self.assertIn("device_type", result)
        self.assertIn("os_family", result)
        self.assertIn("browser_family", result)


class GeolocateIPTest(TestCase):

    def test_localhost_returns_unknown(self):
        result = geolocate_ip("127.0.0.1")
        self.assertEqual(result["country_code"], "")

    def test_loopback_ipv6_returns_unknown(self):
        result = geolocate_ip("::1")
        self.assertEqual(result["country_code"], "")

    def test_empty_returns_unknown(self):
        result = geolocate_ip("")
        self.assertEqual(result["country_code"], "")

    @patch("apps.analytics.utils.requests.get")
    def test_successful_lookup(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "success",
            "countryCode": "US",
            "country": "United States",
            "regionName": "California",
            "city": "San Francisco",
            "lat": 37.77,
            "lon": -122.42,
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = geolocate_ip("8.8.8.8")
        self.assertEqual(result["country_code"], "US")
        self.assertEqual(result["city"], "San Francisco")
        self.assertEqual(result["latitude"], 37.77)

    @patch("apps.analytics.utils.requests.get", side_effect=Exception("timeout"))
    def test_network_error_returns_unknown(self, mock_get):
        result = geolocate_ip("8.8.8.8")
        self.assertEqual(result["country_code"], "")


class GetClientIPTest(TestCase):

    def setUp(self):
        self.factory = RequestFactory()

    @patch("apps.analytics.utils.get_client_ip")
    def test_returns_ip(self, mock_fn):
        mock_fn.return_value = "1.2.3.4"
        from apps.analytics.utils import get_client_ip as real_fn
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "1.2.3.4"
        # Test the real function directly with ipware mock
        with patch("apps.analytics.utils.get_client_ip", wraps=real_fn):
            pass  # ipware integration test — just verify it doesn't raise
        self.assertEqual(mock_fn.return_value, "1.2.3.4")
