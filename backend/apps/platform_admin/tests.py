from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import AuditLog, BusinessVerification, MembershipOrder
from apps.qrcodes.models import Certificate, QRCode, QRSuspiciousReport

User = get_user_model()

CACHE_OVERRIDE = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}


def make_user(email="user@example.com", password="testpass123", **extra):
    user = User.objects.create_user(email=email, password=password, full_name=extra.pop("full_name", "User"), **extra)
    user.is_active = True
    user.is_email_verified = True
    user.save(update_fields=["is_active", "is_email_verified"])
    return user


def make_staff():
    user = make_user(email="admin@example.com", role=User.Role.ADMIN)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])
    return user


@override_settings(CACHES=CACHE_OVERRIDE)
class PlatformAdminTests(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.user = make_user()
        self.qr = QRCode.objects.create(
            user=self.user,
            name="Campaign QR",
            qr_type=QRCode.QRType.DYNAMIC,
            content="https://example.com",
            destination_url="https://example.com",
            total_scans=12,
            unique_scans=7,
        )

    def test_admin_panel_requires_staff_and_renders_overview(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("platform_admin:overview"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.staff)
        response = self.client.get(reverse("platform_admin:overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin overview")
        self.assertContains(response, "QR codes")

    def test_user_detail_updates_plan_and_audit_logs(self):
        self.client.force_login(self.staff)
        expires = (timezone.now() + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(reverse("platform_admin:user_detail", args=[self.user.id]), {
            "full_name": "Updated User",
            "role": User.Role.PRO,
            "plan": "pro",
            "plan_expires_at": expires,
            "is_active": "on",
            "is_email_verified": "on",
        })

        self.assertRedirects(response, reverse("platform_admin:user_detail", args=[self.user.id]))
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Updated User")
        self.assertEqual(self.user.plan, "pro")
        self.assertEqual(self.user.role, User.Role.PRO)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.PROFILE_UPDATE, user=self.staff).exists())

    def test_qrcode_detail_can_pause_qr(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("platform_admin:qrcode_detail", args=[self.qr.id]), {"action": "paused"})

        self.assertRedirects(response, reverse("platform_admin:qrcode_detail", args=[self.qr.id]))
        self.qr.refresh_from_db()
        self.assertEqual(self.qr.status, QRCode.Status.PAUSED)

    def test_certificate_revoke_pauses_attached_qr(self):
        certificate = Certificate.objects.create(
            workspace=self.user,
            qrcode=self.qr,
            recipient_name="Aarav Sharma",
            title="Completion",
            issuer="Narvyn Academy",
            issue_date=timezone.localdate(),
            certificate_id="CERT-ADMIN-1",
        )
        self.client.force_login(self.staff)
        response = self.client.post(reverse("platform_admin:certificate_detail", args=[certificate.id]), {
            "status": Certificate.Status.REVOKED,
        })

        self.assertRedirects(response, reverse("platform_admin:certificate_detail", args=[certificate.id]))
        certificate.refresh_from_db()
        self.qr.refresh_from_db()
        self.assertEqual(certificate.status, Certificate.Status.REVOKED)
        self.assertEqual(self.qr.status, QRCode.Status.PAUSED)

    def test_business_verification_can_be_marked_verified(self):
        verification = BusinessVerification.objects.create(
            workspace=self.user,
            business_name="Narvyn Test",
            domain="example.com",
            method=BusinessVerification.Method.DNS,
        )
        self.client.force_login(self.staff)
        response = self.client.post(reverse("platform_admin:verification_detail", args=[verification.id]), {
            "status": BusinessVerification.Status.VERIFIED,
        })

        self.assertRedirects(response, reverse("platform_admin:verification_detail", args=[verification.id]))
        verification.refresh_from_db()
        self.assertEqual(verification.status, BusinessVerification.Status.VERIFIED)
        self.assertIsNotNone(verification.verified_at)

    def test_report_review_update(self):
        report = QRSuspiciousReport.objects.create(
            qrcode=self.qr,
            scanned_value=self.qr.redirect_url,
            reported_url=self.qr.redirect_url,
            reason=QRSuspiciousReport.Reason.PHISHING,
            risk_level="risky",
        )
        self.client.force_login(self.staff)
        response = self.client.post(reverse("platform_admin:report_detail", args=[report.id]), {
            "status": QRSuspiciousReport.Status.RESOLVED,
            "admin_notes": "Confirmed and handled.",
        })

        self.assertRedirects(response, reverse("platform_admin:report_detail", args=[report.id]))
        report.refresh_from_db()
        self.assertEqual(report.status, QRSuspiciousReport.Status.RESOLVED)
        self.assertEqual(report.admin_notes, "Confirmed and handled.")

    def test_orders_and_audit_pages_render(self):
        MembershipOrder.objects.create(
            user=self.user,
            plan_code="pro",
            billing_cycle=MembershipOrder.BillingCycle.MONTHLY,
            status=MembershipOrder.Status.PAID,
            amount_paise=49900,
            provider_order_id="ORDER123",
            receipt="RCPT123",
        )
        AuditLog.objects.create(user=self.user, action=AuditLog.Action.LOGIN)
        self.client.force_login(self.staff)

        orders_response = self.client.get(reverse("platform_admin:orders"))
        logs_response = self.client.get(reverse("platform_admin:audit_logs"))

        self.assertEqual(orders_response.status_code, 200)
        self.assertContains(orders_response, "ORDER123")
        self.assertEqual(logs_response.status_code, 200)
        self.assertContains(logs_response, "Login")
