from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import AuditLog, BusinessVerification, MembershipOrder
from apps.accounts.plans import PLAN_CATALOG
from apps.analytics.models import DailyQRStats
from apps.barcodes.models import Barcode, BulkBarcodeJob
from apps.platform_admin.forms import (
    AdminCertificateActionForm,
    AdminQRCodeActionForm,
    AdminReportUpdateForm,
    AdminUserUpdateForm,
    AdminVerificationActionForm,
)
from apps.qrcodes.models import (
    Certificate,
    QRCode,
    QRCodeCampaign,
    QRScanEvent,
    QRScannerHistory,
    QRSuspiciousReport,
)

User = get_user_model()


def _page(request, queryset, per_page=25):
    return Paginator(queryset, per_page).get_page(request.GET.get("page", 1))


def _audit(actor, action, request, **metadata):
    AuditLog.objects.create(
        user=actor,
        action=action,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        metadata=metadata,
    )


def _admin_context(active_tab):
    return {
        "admin_active_tab": active_tab,
        "open_report_count": QRSuspiciousReport.objects.filter(
            status__in=[QRSuspiciousReport.Status.OPEN, QRSuspiciousReport.Status.REVIEWING],
        ).count(),
        "pending_verification_count": BusinessVerification.objects.filter(
            status=BusinessVerification.Status.PENDING,
        ).count(),
    }


def _filter_by_date_range(queryset, field, range_code):
    now = timezone.now()
    if range_code == "24h":
        return queryset.filter(**{f"{field}__gte": now - timedelta(hours=24)})
    if range_code == "7d":
        return queryset.filter(**{f"{field}__gte": now - timedelta(days=7)})
    if range_code == "30d":
        return queryset.filter(**{f"{field}__gte": now - timedelta(days=30)})
    return queryset


@staff_member_required
def overview(request):
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    today = timezone.localdate()
    paid_user_filter = (
        Q(role__in=[User.Role.PRO, User.Role.ENTERPRISE, User.Role.ADMIN])
        | (Q(plan__in=["pro", "enterprise"]) & (Q(plan_expires_at__isnull=True) | Q(plan_expires_at__gte=now)))
    )

    qr_totals = QRCode.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status=QRCode.Status.ACTIVE)),
        paused=Count("id", filter=Q(status=QRCode.Status.PAUSED)),
        deleted=Count("id", filter=Q(status=QRCode.Status.DELETED)),
        scans=Coalesce(Sum("total_scans"), 0),
        unique_scans=Coalesce(Sum("unique_scans"), 0),
    )
    report_totals = QRSuspiciousReport.objects.aggregate(
        total=Count("id"),
        open=Count("id", filter=Q(status=QRSuspiciousReport.Status.OPEN)),
        reviewing=Count("id", filter=Q(status=QRSuspiciousReport.Status.REVIEWING)),
        risky=Count("id", filter=Q(risk_level="risky")),
    )
    order_totals = MembershipOrder.objects.aggregate(
        paid=Count("id", filter=Q(status=MembershipOrder.Status.PAID)),
        revenue=Coalesce(Sum("amount_paise", filter=Q(status=MembershipOrder.Status.PAID)), 0),
    )
    daily_scans = list(
        DailyQRStats.objects
        .filter(date__gte=today - timedelta(days=13))
        .values("date")
        .annotate(total=Coalesce(Sum("total_scans"), 0), unique=Coalesce(Sum("unique_scans"), 0))
        .order_by("date")
    )

    context = {
        **_admin_context("overview"),
        "user_totals": {
            "total": User.objects.count(),
            "active": User.objects.filter(is_active=True).count(),
            "staff": User.objects.filter(is_staff=True).count(),
            "paid": User.objects.filter(paid_user_filter).distinct().count(),
            "new_30d": User.objects.filter(date_joined__gte=thirty_days_ago).count(),
        },
        "qr_totals": qr_totals,
        "report_totals": report_totals,
        "order_totals": {
            **order_totals,
            "revenue_inr": (order_totals["revenue"] or 0) / 100,
        },
        "certificate_totals": {
            "total": Certificate.objects.count(),
            "valid": Certificate.objects.filter(status=Certificate.Status.VALID).count(),
            "revoked": Certificate.objects.filter(status=Certificate.Status.REVOKED).count(),
        },
        "verification_totals": {
            "verified": BusinessVerification.objects.filter(status=BusinessVerification.Status.VERIFIED).count(),
            "pending": BusinessVerification.objects.filter(status=BusinessVerification.Status.PENDING).count(),
            "revoked": BusinessVerification.objects.filter(status=BusinessVerification.Status.REVOKED).count(),
        },
        "barcode_totals": {
            "total": Barcode.objects.count(),
            "bulk_jobs": BulkBarcodeJob.objects.count(),
        },
        "daily_scans": daily_scans,
        "recent_users": User.objects.order_by("-date_joined")[:6],
        "recent_qrcodes": QRCode.objects.select_related("user", "campaign").order_by("-created_at")[:6],
        "recent_reports": QRSuspiciousReport.objects.select_related("qrcode", "reporter").order_by("-created_at")[:6],
        "recent_orders": MembershipOrder.objects.select_related("user").order_by("-created_at")[:6],
        "risky_campaigns": QRCodeCampaign.objects.filter(
            risk_status__in=[QRCodeCampaign.RiskStatus.WATCH, QRCodeCampaign.RiskStatus.ELEVATED, QRCodeCampaign.RiskStatus.HIGH],
        ).select_related("user").order_by("-suspicious_report_count")[:6],
    }
    return render(request, "platform_admin/overview.html", context)


@staff_member_required
def users(request):
    qs = User.objects.all().order_by("-date_joined")
    query = request.GET.get("q", "").strip()
    plan = request.GET.get("plan", "").strip()
    status = request.GET.get("status", "").strip()
    if query:
        qs = qs.filter(Q(email__icontains=query) | Q(full_name__icontains=query))
    if plan in PLAN_CATALOG:
        qs = qs.filter(plan=plan)
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)
    elif status == "staff":
        qs = qs.filter(is_staff=True)

    context = {
        **_admin_context("users"),
        "users": _page(request, qs),
        "filters": {"q": query, "plan": plan, "status": status},
        "plan_choices": PLAN_CATALOG.items(),
    }
    return render(request, "platform_admin/users.html", context)


@staff_member_required
def user_detail(request, user_id):
    account = get_object_or_404(User, id=user_id)
    if request.method == "POST":
        form = AdminUserUpdateForm(request.POST, instance=account)
        if form.is_valid():
            changed = form.changed_data.copy()
            updated = form.save(commit=False)
            if updated == request.user and not form.cleaned_data.get("is_staff"):
                updated.is_staff = True
                messages.warning(request, "You cannot remove your own staff access from this panel.")
            updated.save()
            _audit(request.user, AuditLog.Action.PROFILE_UPDATE, request, target_user=str(account.id), changed=changed)
            messages.success(request, "User account updated.")
            return redirect("platform_admin:user_detail", user_id=account.id)
    else:
        form = AdminUserUpdateForm(instance=account)

    context = {
        **_admin_context("users"),
        "account": account,
        "form": form,
        "qrcode_stats": account.qrcodes.aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(status=QRCode.Status.ACTIVE)),
            scans=Coalesce(Sum("total_scans"), 0),
        ),
        "recent_qrcodes": account.qrcodes.select_related("campaign").order_by("-created_at")[:8],
        "recent_orders": account.membership_orders.order_by("-created_at")[:8],
        "recent_reports": account.qr_reports.order_by("-created_at")[:8],
        "recent_certificates": account.certificates.order_by("-created_at")[:8],
        "business_verifications": account.business_verifications.order_by("-updated_at")[:5],
    }
    return render(request, "platform_admin/user_detail.html", context)


@staff_member_required
def qrcodes(request):
    qs = QRCode.objects.select_related("user", "campaign").order_by("-created_at")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    qr_type = request.GET.get("type", "").strip()
    range_code = request.GET.get("range", "").strip()
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(short_code__icontains=query) | Q(user__email__icontains=query))
    if status:
        qs = qs.filter(status=status)
    if qr_type:
        qs = qs.filter(qr_type=qr_type)
    qs = _filter_by_date_range(qs, "created_at", range_code)

    context = {
        **_admin_context("qrcodes"),
        "qrcodes": _page(request, qs),
        "status_choices": QRCode.Status.choices,
        "type_choices": QRCode.QRType.choices,
        "filters": {"q": query, "status": status, "type": qr_type, "range": range_code},
    }
    return render(request, "platform_admin/qrcodes.html", context)


@staff_member_required
def qrcode_detail(request, qr_id):
    qr = get_object_or_404(QRCode.objects.select_related("user", "campaign"), id=qr_id)
    if request.method == "POST":
        form = AdminQRCodeActionForm(request.POST)
        if form.is_valid():
            form.save(qr)
            _audit(request.user, AuditLog.Action.QR_UPDATE, request, qrcode=str(qr.id), status=qr.status)
            messages.success(request, f'QR "{qr.name}" updated.')
            return redirect("platform_admin:qrcode_detail", qr_id=qr.id)
    else:
        form = AdminQRCodeActionForm(initial={"action": qr.status})

    context = {
        **_admin_context("qrcodes"),
        "qr": qr,
        "form": form,
        "recent_scans": qr.scan_events.order_by("-timestamp")[:12],
        "reports": qr.suspicious_reports.order_by("-created_at")[:12],
        "health_check": getattr(qr, "health_check", None),
        "rules": qr.destination_rules.order_by("rule_type", "name"),
    }
    return render(request, "platform_admin/qrcode_detail.html", context)


@staff_member_required
def certificates(request):
    qs = Certificate.objects.select_related("workspace", "qrcode").order_by("-created_at")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if query:
        qs = qs.filter(
            Q(recipient_name__icontains=query)
            | Q(certificate_id__icontains=query)
            | Q(issuer__icontains=query)
            | Q(workspace__email__icontains=query)
        )
    if status:
        qs = qs.filter(status=status)

    context = {
        **_admin_context("certificates"),
        "certificates": _page(request, qs),
        "status_choices": Certificate.Status.choices,
        "filters": {"q": query, "status": status},
    }
    return render(request, "platform_admin/certificates.html", context)


@staff_member_required
def certificate_detail(request, certificate_id):
    certificate = get_object_or_404(Certificate.objects.select_related("workspace", "qrcode"), id=certificate_id)
    if request.method == "POST":
        form = AdminCertificateActionForm(request.POST)
        if form.is_valid():
            form.save(certificate)
            _audit(
                request.user,
                AuditLog.Action.QR_UPDATE,
                request,
                certificate=str(certificate.id),
                status=certificate.status,
            )
            messages.success(request, "Certificate status updated.")
            return redirect("platform_admin:certificate_detail", certificate_id=certificate.id)
    else:
        form = AdminCertificateActionForm(initial={"status": certificate.status})

    return render(request, "platform_admin/certificate_detail.html", {
        **_admin_context("certificates"),
        "certificate": certificate,
        "form": form,
    })


@staff_member_required
def verifications(request):
    qs = BusinessVerification.objects.select_related("workspace").order_by("-updated_at")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if query:
        qs = qs.filter(Q(business_name__icontains=query) | Q(domain__icontains=query) | Q(workspace__email__icontains=query))
    if status:
        qs = qs.filter(status=status)

    context = {
        **_admin_context("verifications"),
        "verifications": _page(request, qs),
        "status_choices": BusinessVerification.Status.choices,
        "filters": {"q": query, "status": status},
    }
    return render(request, "platform_admin/verifications.html", context)


@staff_member_required
def verification_detail(request, verification_id):
    verification = get_object_or_404(BusinessVerification.objects.select_related("workspace"), id=verification_id)
    if request.method == "POST":
        form = AdminVerificationActionForm(request.POST)
        if form.is_valid():
            form.save(verification)
            _audit(
                request.user,
                AuditLog.Action.BUSINESS_REVOKE if verification.status == BusinessVerification.Status.REVOKED else AuditLog.Action.BUSINESS_VERIFY,
                request,
                verification=str(verification.id),
                status=verification.status,
            )
            messages.success(request, "Business verification updated.")
            return redirect("platform_admin:verification_detail", verification_id=verification.id)
    else:
        form = AdminVerificationActionForm(initial={"status": verification.status})

    return render(request, "platform_admin/verification_detail.html", {
        **_admin_context("verifications"),
        "verification": verification,
        "attempts": verification.attempts.select_related("checked_by").order_by("-checked_at")[:20],
        "form": form,
    })


@staff_member_required
def reports(request):
    qs = QRSuspiciousReport.objects.select_related("qrcode", "campaign", "reporter").order_by("-created_at")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    reason = request.GET.get("reason", "").strip()
    risk_level = request.GET.get("risk_level", "").strip()
    if query:
        qs = qs.filter(Q(scanned_value__icontains=query) | Q(reported_url__icontains=query) | Q(host__icontains=query))
    if status:
        qs = qs.filter(status=status)
    if reason:
        qs = qs.filter(reason=reason)
    if risk_level:
        qs = qs.filter(risk_level=risk_level)

    grouped_reports = (
        QRSuspiciousReport.objects.values("qrcode_id", "host")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .order_by("-total")[:8]
    )
    context = {
        **_admin_context("reports"),
        "reports": _page(request, qs),
        "status_choices": QRSuspiciousReport.Status.choices,
        "reason_choices": QRSuspiciousReport.Reason.choices,
        "risk_levels": ["safe", "caution", "risky", "warning", "danger", "unknown"],
        "filters": {"q": query, "status": status, "reason": reason, "risk_level": risk_level},
        "grouped_reports": grouped_reports,
    }
    return render(request, "platform_admin/reports.html", context)


@staff_member_required
def report_detail(request, report_id):
    report = get_object_or_404(
        QRSuspiciousReport.objects.select_related("qrcode", "campaign", "reporter"),
        id=report_id,
    )
    if request.method == "POST":
        form = AdminReportUpdateForm(request.POST, instance=report)
        if form.is_valid():
            form.save()
            _audit(request.user, AuditLog.Action.QR_UPDATE, request, report=str(report.id), status=report.status)
            messages.success(request, "Report review updated.")
            return redirect("platform_admin:report_detail", report_id=report.id)
    else:
        form = AdminReportUpdateForm(instance=report)

    related_count = QRSuspiciousReport.objects.filter(
        Q(qrcode=report.qrcode) if report.qrcode_id else Q(host=report.host),
    ).count()
    return render(request, "platform_admin/report_detail.html", {
        **_admin_context("reports"),
        "report": report,
        "form": form,
        "related_count": related_count,
    })


@staff_member_required
def orders(request):
    qs = MembershipOrder.objects.select_related("user").order_by("-created_at")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    plan = request.GET.get("plan", "").strip()
    if query:
        qs = qs.filter(Q(user__email__icontains=query) | Q(provider_order_id__icontains=query) | Q(receipt__icontains=query))
    if status:
        qs = qs.filter(status=status)
    if plan:
        qs = qs.filter(plan_code=plan)

    context = {
        **_admin_context("orders"),
        "orders": _page(request, qs),
        "status_choices": MembershipOrder.Status.choices,
        "plan_choices": PLAN_CATALOG.items(),
        "filters": {"q": query, "status": status, "plan": plan},
    }
    return render(request, "platform_admin/orders.html", context)


@staff_member_required
def audit_logs(request):
    qs = AuditLog.objects.select_related("user").order_by("-created_at")
    query = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    if query:
        qs = qs.filter(Q(user__email__icontains=query) | Q(metadata__icontains=query) | Q(ip_address__icontains=query))
    if action:
        qs = qs.filter(action=action)

    context = {
        **_admin_context("audit_logs"),
        "logs": _page(request, qs, per_page=30),
        "action_choices": AuditLog.Action.choices,
        "filters": {"q": query, "action": action},
    }
    return render(request, "platform_admin/audit_logs.html", context)
