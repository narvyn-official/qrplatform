from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.tests import make_active_user


class CorePageTests(TestCase):
    def test_home_page_renders_feature_content(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dynamic destinations")
        self.assertContains(response, "Scan analytics")
        self.assertContains(response, "Access controls")
        self.assertContains(response, 'name="description"')
        self.assertContains(response, 'rel="canonical"')
        self.assertContains(response, 'application/ld+json')

    def test_home_page_shows_logout_for_authenticated_users(self):
        self.client.force_login(make_active_user())

        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("qrcodes:dashboard"))
        self.assertContains(response, reverse("accounts:logout"))
        self.assertContains(response, "Logout")

    def test_pricing_page_renders(self):
        response = self.client.get(reverse("core:pricing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Membership plans")
        self.assertContains(response, "UPI checkout")
        self.assertContains(response, "Product")
        self.assertContains(response, 'property="og:title"')

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
        self.assertTrue(any(icon["src"].endswith("icon-512.png") for icon in response.json()["icons"]))
        self.assertTrue(any(shortcut["url"] == "/dashboard/scan/" for shortcut in response.json()["shortcuts"]))

    def test_service_worker_renders_javascript(self):
        response = self.client.get(reverse("core:service_worker"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/javascript", response["Content-Type"])
        self.assertContains(response, "CACHE_NAME")
        self.assertContains(response, "icon-512.png")

    def test_robots_txt_lists_sitemap_and_private_paths(self):
        response = self.client.get(reverse("core:robots_txt"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response["Content-Type"])
        self.assertContains(response, "Disallow: /dashboard/")
        self.assertContains(response, "Disallow: /api/")
        self.assertContains(response, "Sitemap:")

    def test_sitemap_xml_lists_public_pages(self):
        response = self.client.get(reverse("core:sitemap_xml"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/xml", response["Content-Type"])
        self.assertContains(response, "<urlset")
        self.assertContains(response, reverse("core:home"))
        self.assertContains(response, reverse("core:pricing"))
        self.assertContains(response, reverse("core:terms"))

    def test_health_check_reports_database_status(self):
        response = self.client.get(reverse("core:health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @override_settings(CSRF_FAILURE_VIEW="apps.core.views.csrf_failure")
    def test_csrf_failure_redirects_to_fresh_form(self):
        client = Client(enforce_csrf_checks=True)

        response = client.post(
            reverse("accounts:login"),
            {"csrfmiddlewaretoken": "stale-token"},
            HTTP_HOST="testserver",
            HTTP_REFERER="https://testserver/accounts/login/",
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/accounts/login/?csrf=expired")

    def test_csrf_recovery_message_renders(self):
        response = self.client.get(f"{reverse('accounts:login')}?csrf=expired")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Session security refreshed")
