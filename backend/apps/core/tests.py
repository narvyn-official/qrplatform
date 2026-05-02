from django.test import TestCase
from django.urls import reverse


class CorePageTests(TestCase):
    def test_home_page_renders_feature_content(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dynamic destinations")
        self.assertContains(response, "Scan analytics")
        self.assertContains(response, "Access controls")

    def test_pricing_page_renders(self):
        response = self.client.get(reverse("core:pricing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Simple, transparent pricing")

    def test_terms_page_renders(self):
        response = self.client.get(reverse("core:terms"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Terms of Service")

    def test_offline_page_renders(self):
        response = self.client.get(reverse("core:offline"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You are offline")

    def test_manifest_renders_pwa_metadata(self):
        response = self.client.get(reverse("core:manifest"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        self.assertEqual(response.json()["display"], "standalone")

    def test_service_worker_renders_javascript(self):
        response = self.client.get(reverse("core:service_worker"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/javascript", response["Content-Type"])
        self.assertContains(response, "CACHE_NAME")

    def test_health_check_reports_database_status(self):
        response = self.client.get(reverse("core:health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
