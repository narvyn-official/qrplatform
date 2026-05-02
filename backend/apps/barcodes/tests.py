"""
Comprehensive tests for the barcodes app.
Covers: models, forms, and views.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

from apps.barcodes.models import Barcode, BulkBarcodeJob
from apps.barcodes.forms import BarcodeForm, BulkBarcodeForm
from apps.barcodes.utils import validate_barcode_content

User = get_user_model()

CACHE_OVERRIDE = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}


def make_active_user(email="bc@example.com", password="testpass123", **kw):
    user = User.objects.create_user(
        email=email, password=password,
        full_name=kw.pop("full_name", "Barcode Tester"), **kw,
    )
    user.is_active = True
    user.is_email_verified = True
    user.save(update_fields=["is_active", "is_email_verified"])
    return user


def make_barcode(user, **kw):
    defaults = {
        "name": "Test Barcode",
        "barcode_format": Barcode.BarcodeFormat.CODE128,
        "content": "1234567890",
    }
    defaults.update(kw)
    return Barcode.objects.create(user=user, **defaults)


# ── Model tests ───────────────────────────────────────────────────────────────

class BarcodeModelTest(TestCase):

    def setUp(self):
        self.user = make_active_user()

    def test_str(self):
        bc = make_barcode(self.user, name="My Barcode")
        self.assertIn("My Barcode", str(bc))

    def test_default_dimensions(self):
        bc = make_barcode(self.user)
        self.assertEqual(bc.width, 300)
        self.assertEqual(bc.height, 100)

    def test_uuid_primary_key(self):
        bc = make_barcode(self.user)
        import uuid
        self.assertIsInstance(bc.id, uuid.UUID)


class BulkBarcodeJobModelTest(TestCase):

    def setUp(self):
        self.user = make_active_user()

    def test_str(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        job = BulkBarcodeJob.objects.create(
            user=self.user, name="Bulk Job",
            barcode_format=Barcode.BarcodeFormat.CODE128,
            source_file=SimpleUploadedFile("data.csv", b"a,b,c"),
        )
        self.assertIn("Bulk Job", str(job))

    def test_progress_percent_zero_when_empty(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        job = BulkBarcodeJob.objects.create(
            user=self.user, name="Empty Job",
            barcode_format=Barcode.BarcodeFormat.CODE128,
            source_file=SimpleUploadedFile("data.csv", b"a"),
            total_count=0,
        )
        self.assertEqual(job.progress_percent, 0)

    def test_progress_percent_calculation(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        job = BulkBarcodeJob.objects.create(
            user=self.user, name="Half Job",
            barcode_format=Barcode.BarcodeFormat.CODE128,
            source_file=SimpleUploadedFile("data.csv", b"a"),
            total_count=10, processed_count=5,
        )
        self.assertEqual(job.progress_percent, 50)


# ── Form tests ────────────────────────────────────────────────────────────────

class BarcodeFormTest(TestCase):

    def test_valid_form(self):
        form = BarcodeForm({
            "name": "My Code",
            "barcode_format": "code128",
            "content": "HELLO123",
            "foreground_color": "#000000",
            "background_color": "#FFFFFF",
            "show_text": True,
            "width": 300,
            "height": 100,
            "font_size": 10,
            "tags": "[]",
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_name_required(self):
        form = BarcodeForm({
            "name": "",
            "barcode_format": "code128",
            "content": "HELLO",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_content_required(self):
        form = BarcodeForm({
            "name": "BC",
            "barcode_format": "code128",
            "content": "",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("content", form.errors)

    def test_upce_is_rejected_instead_of_silent_fallback(self):
        form = BarcodeForm({
            "name": "UPC-E",
            "barcode_format": "upce",
            "content": "123456",
            "foreground_color": "#000000",
            "background_color": "#FFFFFF",
            "show_text": True,
            "width": 300,
            "height": 100,
            "font_size": 10,
            "tags": "[]",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("barcode_format", form.errors)

    def test_validate_upce_reports_unsupported(self):
        ok, error = validate_barcode_content("123456", "upce")
        self.assertFalse(ok)
        self.assertIn("not supported", error)


class BulkBarcodeFormTest(TestCase):

    def test_rejects_non_csv(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        bad = SimpleUploadedFile("data.txt", b"data", content_type="text/plain")
        form = BulkBarcodeForm({
            "name": "Bulk",
            "barcode_format": "code128",
        }, files={"source_file": bad})
        self.assertFalse(form.is_valid())
        self.assertIn("source_file", form.errors)

    def test_rejects_large_csv(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        big = SimpleUploadedFile("data.csv", b"x" * (6 * 1024 * 1024), content_type="text/csv")
        form = BulkBarcodeForm({
            "name": "Big",
            "barcode_format": "code128",
        }, files={"source_file": big})
        self.assertFalse(form.is_valid())
        self.assertIn("source_file", form.errors)


# ── View tests ────────────────────────────────────────────────────────────────

@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class BarcodeListViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()
        self.client.force_login(self.user)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("barcodes:list"))
        self.assertEqual(resp.status_code, 302)

    def test_renders_empty(self):
        resp = self.client.get(reverse("barcodes:list"))
        self.assertEqual(resp.status_code, 200)

    def test_shows_only_user_barcodes(self):
        make_barcode(self.user, name="Mine")
        other = make_active_user(email="other@bc.com")
        make_barcode(other, name="Theirs")
        resp = self.client.get(reverse("barcodes:list"))
        names = [bc.name for bc in resp.context["barcodes"]]
        self.assertIn("Mine", names)
        self.assertNotIn("Theirs", names)


@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class BarcodeCreateViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()
        self.client.force_login(self.user)

    def test_get_renders_form(self):
        resp = self.client.get(reverse("barcodes:create"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("form", resp.context)
        self.assertContains(resp, "Font Size")
        self.assertNotContains(resp, 'value="upce"')

    @patch("apps.barcodes.views.generate_barcode_png", return_value=b"\x89PNG\r\n")
    @patch("apps.barcodes.views.generate_barcode_svg", return_value="<svg/>")
    @patch("apps.barcodes.views.validate_barcode_content", return_value=(True, None))
    def test_post_valid_creates_barcode(self, mock_validate, mock_svg, mock_png):
        resp = self.client.post(reverse("barcodes:create"), {
            "name": "New Barcode",
            "barcode_format": "code128",
            "content": "ABC123",
            "foreground_color": "#000000",
            "background_color": "#FFFFFF",
            "show_text": True,
            "width": 300,
            "height": 100,
            "font_size": 10,
            "tags": "[]",
        })
        self.assertEqual(Barcode.objects.filter(user=self.user, name="New Barcode").count(), 1)
        self.assertEqual(resp.status_code, 302)

    @patch("apps.barcodes.views.validate_barcode_content", return_value=(False, "Invalid content"))
    def test_post_invalid_content_rerenders(self, mock_validate):
        resp = self.client.post(reverse("barcodes:create"), {
            "name": "Bad Barcode",
            "barcode_format": "ean13",
            "content": "bad",
            "foreground_color": "#000000",
            "background_color": "#FFFFFF",
            "show_text": True,
            "width": 300,
            "height": 100,
            "font_size": 10,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Barcode.objects.filter(user=self.user).count(), 0)

    def test_post_missing_name_rerenders(self):
        resp = self.client.post(reverse("barcodes:create"), {
            "name": "",
            "barcode_format": "code128",
            "content": "ABC",
        })
        self.assertEqual(resp.status_code, 200)

    def test_post_real_generation_creates_downloadable_files(self):
        resp = self.client.post(reverse("barcodes:create"), {
            "name": "Real Barcode",
            "barcode_format": "code128",
            "content": "ABC123",
            "foreground_color": "#111111",
            "background_color": "#ffffff",
            "show_text": True,
            "width": 300,
            "height": 100,
            "font_size": 10,
            "tags": "[]",
        })
        self.assertEqual(resp.status_code, 302)
        bc = Barcode.objects.get(user=self.user, name="Real Barcode")
        self.assertTrue(bc.image_png)
        self.assertTrue(bc.image_svg.startswith("<?xml"))


@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class BarcodeDetailViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()
        self.client.force_login(self.user)

    def test_detail_renders(self):
        bc = make_barcode(self.user)
        resp = self.client.get(reverse("barcodes:detail", args=[bc.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["barcode"], bc)

    def test_detail_forbidden_for_other_user(self):
        other = make_active_user(email="other2@bc.com")
        bc = make_barcode(other)
        resp = self.client.get(reverse("barcodes:detail", args=[bc.id]))
        self.assertEqual(resp.status_code, 404)


@override_settings(CACHES=CACHE_OVERRIDE, AXES_ENABLED=False)
class BarcodeDeleteViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_active_user()
        self.client.force_login(self.user)

    def test_delete_removes_barcode(self):
        bc = make_barcode(self.user)
        bc_id = bc.id
        resp = self.client.post(reverse("barcodes:delete", args=[bc.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Barcode.objects.filter(id=bc_id).exists())

    def test_delete_requires_post(self):
        bc = make_barcode(self.user)
        resp = self.client.get(reverse("barcodes:delete", args=[bc.id]))
        self.assertEqual(resp.status_code, 405)

    def test_delete_forbidden_for_other_user(self):
        other = make_active_user(email="other3@bc.com")
        bc = make_barcode(other)
        resp = self.client.post(reverse("barcodes:delete", args=[bc.id]))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Barcode.objects.filter(id=bc.id).exists())
