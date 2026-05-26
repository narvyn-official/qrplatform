"""
QR Code views — dashboard CRUD and dynamic redirect handler.
"""
import io
import csv
import json
import logging
import zipfile
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

from django.shortcuts import render, get_object_or_404, redirect
from django.utils.text import slugify
from django.utils.dateparse import parse_date
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import (
    HttpResponse, JsonResponse, Http404, HttpResponseForbidden
)
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.cache import never_cache
from django.core.paginator import Paginator
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.conf import settings

from apps.qrcodes.models import (
    QRCode,
    QRScanEvent,
    QRCodeCampaign,
    QRDestinationRule,
    QRHealthCheck,
    QRLandingPage,
    QRConversionEvent,
    QRSuspiciousReport,
    QRScannerHistory,
    Certificate,
)
from apps.accounts.models import BusinessVerification
from apps.qrcodes.forms import CertificateBulkUploadForm, CertificateForm, QRCodeForm
from apps.qrcodes.premium import assess_destination, build_preprint_check, resolve_smart_destination
from apps.qrcodes.safety import analyze_scanned_value, analyze_scanner_content, analyze_url
from apps.qrcodes.services import active_qr_count, can_create_qr, qr_quota_message
from apps.qrcodes.template_gallery import (
    TEMPLATE_CATEGORIES,
    build_template_qr_data,
    get_template,
    template_context,
    user_can_use_template,
)
from apps.qrcodes.tasks import generate_qr_images
from apps.analytics.utils import get_client_ip, compute_scan_fingerprint
from apps.analytics.tasks import process_scan_event, process_scan_event_now
from apps.analytics.selectors import account_daily_scan_series, account_lifetime_scan_totals

logger = logging.getLogger(__name__)


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _rate_limited(request, bucket, *, limit=30, window=60):
    ip = get_client_ip(request) or "unknown"
    key = f"qr:{bucket}:{ip}"
    count = cache.get(key, 0)
    if count >= limit:
        return True
    cache.set(key, count + 1, window)
    return False


def _qrcode_for_scanned_content(content, request):
    raw = (content or "").strip()
    if not raw.lower().startswith(("http://", "https://")):
        return None

    parsed = urlparse(raw)
    if not parsed.hostname:
        return None

    candidate_hosts = {
        urlparse(settings.QR_REDIRECT_BASE).netloc.lower(),
        urlparse(settings.PLATFORM_URL).netloc.lower(),
        request.get_host().lower(),
    }
    if parsed.netloc.lower() not in candidate_hosts:
        return None

    redirect_base = urlparse(settings.QR_REDIRECT_BASE).path.strip("/").split("/")
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if not path_parts:
        return None

    short_code = ""
    if redirect_base and redirect_base[0] and path_parts[:len(redirect_base)] == redirect_base:
        if len(path_parts) > len(redirect_base):
            short_code = path_parts[len(redirect_base)]
    elif path_parts[0] == "r" and len(path_parts) > 1:
        short_code = path_parts[1]
    if not short_code:
        return None

    return QRCode.objects.select_related("user", "campaign").filter(short_code=short_code).exclude(status=QRCode.Status.DELETED).first()


def _verified_business_for_scanned_content(content, request):
    qr = _qrcode_for_scanned_content(content, request)
    if not qr:
        return None
    verification = (
        BusinessVerification.objects
        .filter(workspace=qr.user, status=BusinessVerification.Status.VERIFIED)
        .order_by("-verified_at")
        .first()
    )
    if not verification:
        return None
    return {
        "verified": True,
        "businessName": verification.business_name,
        "domain": verification.domain,
        "verifiedAt": verification.verified_at.isoformat() if verification.verified_at else "",
        "verifiedDate": verification.verified_at.strftime("%b %d, %Y") if verification.verified_at else "",
        "badgeText": "Verified by Narvyn",
    }


def _report_url_from_analysis(analysis, fallback=""):
    if not analysis:
        return fallback
    return (
        analysis.get("finalUrl")
        or analysis.get("normalizedUrl")
        or analysis.get("rawContent")
        or fallback
    )


def _refresh_campaign_risk(campaign):
    if not campaign:
        return
    open_count = QRSuspiciousReport.objects.filter(
        campaign=campaign,
        status__in=[QRSuspiciousReport.Status.OPEN, QRSuspiciousReport.Status.REVIEWING],
    ).count()
    if open_count >= 10:
        risk_status = QRCodeCampaign.RiskStatus.HIGH
    elif open_count >= 5:
        risk_status = QRCodeCampaign.RiskStatus.ELEVATED
    elif open_count > 0:
        risk_status = QRCodeCampaign.RiskStatus.WATCH
    else:
        risk_status = QRCodeCampaign.RiskStatus.CLEAR
    QRCodeCampaign.objects.filter(pk=campaign.pk).update(
        suspicious_report_count=open_count,
        risk_status=risk_status,
        updated_at=timezone.now(),
    )


def _create_qr_report(request, *, content, reason, comment="", analysis=None):
    allowed_reasons = {choice[0] for choice in QRSuspiciousReport.Reason.choices}
    reason = reason if reason in allowed_reasons else QRSuspiciousReport.Reason.OTHER
    qr = _qrcode_for_scanned_content(content, request)
    campaign = qr.campaign if qr else None
    parsed_url = urlparse(_report_url_from_analysis(analysis, content))
    host = (parsed_url.hostname or "").lower()
    if not analysis:
        analysis = analyze_scanner_content(content, fetch_title=False)
    report = QRSuspiciousReport.objects.create(
        qrcode=qr,
        campaign=campaign,
        reporter=request.user if request.user.is_authenticated else None,
        scanned_value=content,
        normalized_url=analysis.get("normalizedUrl", "") or analysis.get("finalUrl", ""),
        reported_url=_report_url_from_analysis(analysis, ""),
        host=host or analysis.get("domain", ""),
        reason=reason,
        comment=comment,
        risk_level=analysis.get("riskLevel", "unknown"),
        safety_snapshot=analysis,
        ip_address=get_client_ip(request),
        user_agent_raw=request.META.get("HTTP_USER_AGENT", "")[:1000],
    )
    _refresh_campaign_risk(campaign)
    return report


def _qrcode_form_data(post_data):
    """
    Return mutable form data with a reliable single content value.

    Older templates emitted multiple controls named ``content``. Browsers submit
    hidden controls too, so the final empty field could overwrite the real URL.
    Keep this normalizer so existing clients and cached pages still submit
    correctly.
    """
    data = post_data.copy()
    content_values = [value.strip() for value in post_data.getlist("content") if value and value.strip()]
    if content_values:
        data["content"] = content_values[0]
    return data


def _generate_qr_now(qr):
    try:
        generate_qr_images(qr)
        return True
    except Exception as exc:
        logger.exception("Failed to generate QR images for %s: %s", qr.id, exc)
        return False


def _validation_error_dict(error):
    if hasattr(error, "message_dict"):
        return {key: " ".join(messages) for key, messages in error.message_dict.items()}
    return {"__all__": " ".join(error.messages)}


def _certificate_campaign(user):
    campaign = QRCodeCampaign.objects.filter(user=user, name="Certificate verification", is_active=True).first()
    if campaign:
        return campaign
    return QRCodeCampaign.objects.create(
        user=user,
        name="Certificate verification",
        description="Verification QR codes issued from certificate records.",
        tags=["certificates", "verification"],
        color="#14B8A6",
    )


def _create_certificate_qr(request, certificate):
    verification_url = request.build_absolute_uri(certificate.verify_path)
    qr = QRCode.objects.create(
        user=certificate.workspace,
        campaign=_certificate_campaign(certificate.workspace),
        name=f"Certificate verification - {certificate.certificate_id}",
        qr_type=QRCode.QRType.DYNAMIC,
        content=verification_url,
        destination_url=verification_url,
        tags=["certificate", certificate.certificate_id],
        foreground_color="#07111F",
        background_color="#FFFFFF",
        frame_text="Verify certificate",
        frame_color="#07111F",
        error_correction="M",
        qr_size=300,
    )
    certificate.qrcode = qr
    certificate.save(update_fields=["qrcode", "updated_at"])
    _generate_qr_now(qr)
    return qr


def _certificate_queryset_for_user(user):
    qs = Certificate.objects.select_related("qrcode", "workspace")
    if user.is_staff:
        return qs
    return qs.filter(workspace=user)


def _parse_certificate_csv_date(value, field, row_number):
    if not value:
        return None
    parsed = parse_date(value)
    if not parsed:
        raise ValidationError({field: f"Row {row_number}: use YYYY-MM-DD for {field}."})
    return parsed


def _tracked_content_response(request, qr):
    """Return the stored QR payload after scan counting has completed."""
    destination = (qr.destination_url if qr.is_dynamic else qr.content) or ""
    if not destination:
        return render(request, "qrcodes/expired.html", {"qr": qr}, status=410)

    if qr.qr_type in (QRCode.QRType.URL, QRCode.QRType.DYNAMIC, QRCode.QRType.WHATSAPP):
        resolution = resolve_smart_destination(qr, request, destination)
        destination = resolution.url

        if qr.utm_params:
            try:
                parsed = urlparse(destination)
                qs = parse_qs(parsed.query, keep_blank_values=True)
                for k, v in qr.utm_params.items():
                    if v:
                        qs[k] = [v]
                destination = urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))
            except Exception:
                pass

        return redirect(destination, permanent=False)

    if qr.qr_type == QRCode.QRType.VCARD:
        filename = slugify(qr.name) or qr.short_code
        response = HttpResponse(destination, content_type="text/vcard; charset=utf-8")
        response["Content-Disposition"] = f'inline; filename="{filename}.vcf"'
        return response

    if qr.qr_type in (QRCode.QRType.EMAIL, QRCode.QRType.SMS):
        label = "Open email app" if qr.qr_type == QRCode.QRType.EMAIL else "Open SMS app"
        return render(request, "qrcodes/tracked_action.html", {
            "qr": qr,
            "title": qr.name,
            "action_label": label,
            "action_url": destination,
            "content": destination,
        })

    return render(request, "qrcodes/tracked_content.html", {
        "qr": qr,
        "title": qr.name,
        "content": destination,
    })



# ── Dashboard views ──────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    user = request.user
    qrcodes = QRCode.objects.filter(user=user).exclude(status="deleted").order_by("-created_at")
    active_qrcodes = qrcodes.filter(status__in=["active", "paused"])

    total_qr = active_qrcodes.count()
    scan_totals = qrcodes.aggregate(
        total=Sum("total_scans"),
        unique=Sum("unique_scans"),
    )
    weekly_stats = account_daily_scan_series(user, days=7)
    weekly_total_scans = sum(row["total_scans"] for row in weekly_stats)
    weekly_unique_scans = sum(row["unique_scans"] for row in weekly_stats)
    stored_total_scans = scan_totals["total"] or 0
    stored_unique_scans = scan_totals["unique"] or 0
    analytics_totals = account_lifetime_scan_totals(user)
    total_scans = max(
        stored_total_scans,
        analytics_totals["total_scans"],
        weekly_total_scans,
    )
    unique_scans = max(
        stored_unique_scans,
        analytics_totals["unique_scans"],
        weekly_unique_scans,
    )
    open_report_count = QRSuspiciousReport.objects.filter(
        qrcode__user=user,
        status__in=[QRSuspiciousReport.Status.OPEN, QRSuspiciousReport.Status.REVIEWING],
    ).count()
    risky_campaigns = QRCodeCampaign.objects.filter(
        user=user,
        risk_status__in=[QRCodeCampaign.RiskStatus.ELEVATED, QRCodeCampaign.RiskStatus.HIGH],
    ).order_by("-suspicious_report_count")[:5]
    limits = user.plan_limits
    qr_limit = limits.get("max_qr", 0)
    remaining_qr = None if qr_limit < 0 else max(qr_limit - total_qr, 0)

    launch_checklist = [
        {
            "label": "Create a dynamic QR",
            "desc": "Publish one editable code before sharing any printed material.",
            "done": total_qr > 0,
            "url": "/dashboard/qrcodes/create/",
        },
        {
            "label": "Test the scan flow",
            "desc": "Use the scanner once so customers land exactly where you expect.",
            "done": total_scans > 0,
            "url": "/dashboard/scan/",
        },
        {
            "label": "Review early scan signals",
            "desc": "Watch total and unique scans before spending on more prints.",
            "done": weekly_total_scans > 0 or total_scans > 0,
            "url": "/dashboard/qrcodes/",
        },
        {
            "label": "Unlock campaign tools",
            "desc": "Upgrade for branded exports, UTM tags, schedules, API keys, and higher limits.",
            "done": user.is_pro,
            "url": "/pricing/",
        },
    ]
    completed_steps = sum(1 for step in launch_checklist if step["done"])

    if total_qr == 0:
        recommended_action = {
            "label": "Create your first QR",
            "desc": "Start with a dynamic URL so you can edit the destination later.",
            "url": "/dashboard/qrcodes/create/",
        }
    elif total_scans == 0:
        recommended_action = {
            "label": "Test a scan now",
            "desc": "Confirm the customer experience before printing or sharing.",
            "url": "/dashboard/scan/",
        }
    elif not user.is_pro:
        recommended_action = {
            "label": "Upgrade for campaigns",
            "desc": "Add branded exports, UTM tracking, schedules, and higher limits.",
            "url": "/pricing/",
        }
    else:
        recommended_action = {
            "label": "Export campaign assets",
            "desc": "Download production files or package all QR codes into a ZIP.",
            "url": "/dashboard/qrcodes/",
        }

    # Recent QR codes
    recent_qrcodes = qrcodes[:5]
    top_qrcodes = qrcodes.order_by("-total_scans", "-created_at")[:5]

    quick_actions = [
        {"url": "/dashboard/qrcodes/create/", "label": "New QR Code", "desc": "URL, vCard, WiFi…", "bg": "bg-primary-50", "icon": '<svg class="w-5 h-5 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>'},
        {"url": "/barcodes/create/", "label": "New Barcode", "desc": "Code128, EAN, QR…", "bg": "bg-blue-50", "icon": '<svg class="w-5 h-5 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7"/></svg>'},
        {"url": "/dashboard/scan/", "label": "Scan QR", "desc": "Camera & upload", "bg": "bg-emerald-50", "icon": '<svg class="w-5 h-5 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7V5a2 2 0 012-2h2m10 0h2a2 2 0 012 2v2M3 17v2a2 2 0 002 2h2m10 0h2a2 2 0 002-2v-2M8 12h8m-4-4v8"/></svg>'},
        {"url": "/dashboard/qrcodes/", "label": "My QR Codes", "desc": "Manage & analyse", "bg": "bg-green-50", "icon": '<svg class="w-5 h-5 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 10h16M4 14h16M4 18h16"/></svg>'},
        {"url": "/accounts/profile/", "label": "Profile & API", "desc": "Settings & keys", "bg": "bg-purple-50", "icon": '<svg class="w-5 h-5 text-purple-600" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>'},
    ]
    context = {
        "total_qr": total_qr,
        "total_scans": total_scans,
        "unique_scans": unique_scans,
        "weekly_stats": weekly_stats,
        "weekly_total_scans": weekly_total_scans,
        "weekly_unique_scans": weekly_unique_scans,
        "open_report_count": open_report_count,
        "risky_campaigns": risky_campaigns,
        "recent_qrcodes": recent_qrcodes,
        "top_qrcodes": top_qrcodes,
        "quick_actions": quick_actions,
        "launch_checklist": launch_checklist,
        "completed_steps": completed_steps,
        "remaining_qr": remaining_qr,
        "qr_limit_unlimited": qr_limit < 0,
        "recommended_action": recommended_action,
        "active_tab": "dashboard",
    }
    return render(request, "dashboard/index.html", context)


@login_required
def qrcode_list(request):
    user = request.user
    qs = QRCode.objects.filter(user=user).exclude(status="deleted").annotate(
        open_report_count=Count(
            "suspicious_reports",
            filter=Q(suspicious_reports__status__in=[
                QRSuspiciousReport.Status.OPEN,
                QRSuspiciousReport.Status.REVIEWING,
            ]),
        )
    )

    # Search & filter
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(tags__icontains=q))

    qr_type = request.GET.get("type", "")
    if qr_type:
        qs = qs.filter(qr_type=qr_type)

    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status)

    campaign_id = request.GET.get("campaign", "")
    if campaign_id:
        qs = qs.filter(campaign_id=campaign_id)

    qs = qs.order_by("-created_at")
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))

    campaigns = QRCodeCampaign.objects.filter(user=user, is_active=True)

    context = {
        "qrcodes": page,
        "campaigns": campaigns,
        "qr_types": QRCode.QRType.choices,
        "active_tab": "qrcodes",
        "query": q,
    }
    return render(request, "qrcodes/list.html", context)


@login_required
def qrcode_create(request):
    if not can_create_qr(request.user):
        messages.error(request, qr_quota_message(request.user, action="create"))
        return redirect("core:pricing")

    if request.method == "POST":
        form = QRCodeForm(_qrcode_form_data(request.POST), request.FILES, user=request.user)
        if form.is_valid():
            qr = form.save(commit=False)
            qr.user = request.user
            qr.save()
            if _generate_qr_now(qr):
                messages.success(request, "QR code created and ready to download.")
            else:
                messages.warning(request, "QR code created, but image generation failed. Try downloading again.")
            return redirect("qrcodes:detail", pk=qr.id)
    else:
        form = QRCodeForm(user=request.user)

    context = {
        "form": form,
        "active_tab": "create_qr",
        "qr_types": QRCode.QRType.choices,
    }
    return render(request, "qrcodes/create.html", context)


@login_required
def qrcode_template_gallery(request):
    if not can_create_qr(request.user):
        messages.error(request, qr_quota_message(request.user, action="create"))
        return redirect("core:pricing")

    context = {
        "templates": template_context(request.user),
        "categories": TEMPLATE_CATEGORIES,
        "active_tab": "templates",
    }
    return render(request, "qrcodes/templates/gallery.html", context)


@login_required
def qrcode_template_create(request, slug):
    if not can_create_qr(request.user):
        messages.error(request, qr_quota_message(request.user, action="create"))
        return redirect("core:pricing")

    if slug == "certificate-verification":
        return redirect("qrcodes:certificate_new")

    try:
        template = get_template(slug)
    except ValidationError:
        raise Http404

    if not user_can_use_template(request.user, template):
        messages.error(request, f"{template.title} requires the {template.plan_label} plan.")
        return redirect("core:pricing")

    values = request.POST if request.method == "POST" else {}
    errors = {}
    if request.method == "POST":
        try:
            payload = build_template_qr_data(template, request.POST)
        except ValidationError as error:
            errors = _validation_error_dict(error)
        else:
            with transaction.atomic():
                campaign = QRCodeCampaign.objects.create(
                    user=request.user,
                    name=payload["campaign_name"],
                    description=f"Created from the {template.title} QR template.",
                    tags=payload["tags"],
                    color="#14B8A6",
                )
                qr = QRCode.objects.create(
                    user=request.user,
                    campaign=campaign,
                    name=payload["name"],
                    qr_type=payload["qr_type"],
                    content=payload["content"],
                    destination_url=payload["destination_url"],
                    tags=payload["tags"],
                    error_correction="M",
                    qr_size=300,
                )
            if _generate_qr_now(qr):
                messages.success(request, f"{template.title} QR created from template.")
            else:
                messages.warning(request, "QR code created, but image generation failed. Try downloading again.")
            return redirect("qrcodes:detail", pk=qr.id)

    context = {
        "template": template,
        "field_states": [
            {
                "field": field,
                "value": values.get(field.name, "") if values else "",
                "error": errors.get(field.name, ""),
                "checked": values.get(field.name) in ("on", "true", "1") if values else False,
            }
            for field in template.fields
        ],
        "name_value": values.get("name", "") if values else "",
        "campaign_name_value": values.get("campaign_name", "") if values else "",
        "non_field_error": errors.get("__all__", ""),
        "values": values,
        "errors": errors,
        "active_tab": "templates",
    }
    return render(request, "qrcodes/templates/create.html", context)


@login_required
def certificate_list(request):
    certificates = Certificate.objects.filter(workspace=request.user).select_related("qrcode").order_by("-created_at")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if query:
        certificates = certificates.filter(
            Q(recipient_name__icontains=query)
            | Q(title__icontains=query)
            | Q(issuer__icontains=query)
            | Q(certificate_id__icontains=query)
        )
    if status in {choice[0] for choice in Certificate.Status.choices}:
        certificates = certificates.filter(status=status)

    paginator = Paginator(certificates, 20)
    page = paginator.get_page(request.GET.get("page", 1))
    today = timezone.localdate()
    base_certificates = Certificate.objects.filter(workspace=request.user)
    stats = {
        "total": base_certificates.count(),
        "valid": base_certificates.filter(status=Certificate.Status.VALID).filter(
            Q(expiry_date__isnull=True) | Q(expiry_date__gte=today)
        ).count(),
        "revoked": base_certificates.filter(status=Certificate.Status.REVOKED).count(),
    }
    stats["expired"] = Certificate.objects.filter(
        workspace=request.user,
        expiry_date__lt=today,
    ).exclude(status=Certificate.Status.REVOKED).count()

    return render(request, "qrcodes/certificates/list.html", {
        "certificates": page,
        "bulk_form": CertificateBulkUploadForm(),
        "query": query,
        "stats": stats,
        "active_tab": "certificates",
    })


@login_required
def certificate_new(request):
    if not can_create_qr(request.user):
        messages.error(request, qr_quota_message(request.user, action="issue certificate QR codes"))
        return redirect("core:pricing")

    if request.method == "POST":
        form = CertificateForm(request.POST, request.FILES, workspace=request.user)
        if form.is_valid():
            with transaction.atomic():
                certificate = form.save(commit=False)
                certificate.workspace = request.user
                certificate.save()
                _create_certificate_qr(request, certificate)
            messages.success(request, "Certificate verification QR created.")
            return redirect("qrcodes:certificate_detail", pk=certificate.pk)
    else:
        form = CertificateForm(workspace=request.user)

    return render(request, "qrcodes/certificates/new.html", {
        "form": form,
        "active_tab": "certificates",
    })


@login_required
def certificate_detail(request, pk):
    certificate = get_object_or_404(_certificate_queryset_for_user(request.user), pk=pk)
    return render(request, "qrcodes/certificates/detail.html", {
        "certificate": certificate,
        "active_tab": "certificates",
    })


@login_required
@require_POST
def certificate_revoke(request, pk):
    certificate = get_object_or_404(_certificate_queryset_for_user(request.user), pk=pk)
    if certificate.status != Certificate.Status.REVOKED:
        certificate.status = Certificate.Status.REVOKED
        certificate.revoked_at = timezone.now()
        certificate.save(update_fields=["status", "revoked_at", "updated_at"])
        if certificate.qrcode:
            QRCode.objects.filter(pk=certificate.qrcode_id).update(status=QRCode.Status.PAUSED, updated_at=timezone.now())
        messages.warning(request, "Certificate revoked. Public verification now shows a warning.")
    else:
        messages.info(request, "Certificate was already revoked.")
    return redirect("qrcodes:certificate_detail", pk=certificate.pk)


@login_required
@require_POST
def certificate_bulk_upload(request):
    form = CertificateBulkUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, form.errors.get("csv_file", ["Upload a valid CSV file."])[0])
        return redirect("qrcodes:certificate_list")

    try:
        rows = form.parsed_rows()
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("qrcodes:certificate_list")

    max_qr = request.user.plan_limits["max_qr"]
    if max_qr > 0 and active_qr_count(request.user) + len(rows) > max_qr:
        messages.error(request, qr_quota_message(request.user, action="bulk upload certificates"))
        return redirect("qrcodes:certificate_list")

    seen_ids = set()
    created = []
    row_errors = []
    try:
        with transaction.atomic():
            for row in rows:
                row_number = row["_row_number"]
                certificate_id = row.get("certificate_id", "").strip()
                if certificate_id.lower() in seen_ids:
                    row_errors.append(f"Row {row_number}: duplicate certificate_id in CSV.")
                    continue
                seen_ids.add(certificate_id.lower())
                try:
                    issue_date = _parse_certificate_csv_date(row.get("issue_date", ""), "issue_date", row_number)
                    expiry_date = _parse_certificate_csv_date(row.get("expiry_date", ""), "expiry_date", row_number)
                    certificate = Certificate(
                        workspace=request.user,
                        recipient_name=row.get("recipient_name", ""),
                        title=row.get("title", ""),
                        issuer=row.get("issuer", ""),
                        issue_date=issue_date,
                        expiry_date=expiry_date,
                        certificate_id=certificate_id,
                    )
                    certificate.full_clean(exclude=["qrcode", "pdf_url", "verification_code"])
                    certificate.save()
                    _create_certificate_qr(request, certificate)
                    created.append(certificate)
                except ValidationError as error:
                    if hasattr(error, "message_dict"):
                        details = "; ".join(f"{field}: {' '.join(msgs)}" for field, msgs in error.message_dict.items())
                    else:
                        details = " ".join(error.messages)
                    row_errors.append(f"Row {row_number}: {details}")

            if row_errors:
                raise ValidationError(row_errors[:5])
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("qrcodes:certificate_list")

    messages.success(request, f"Bulk upload complete. Created {len(created)} certificate QR code{'' if len(created) == 1 else 's'}.")
    return redirect("qrcodes:certificate_list")


def certificate_verify(request, code):
    certificate = get_object_or_404(
        Certificate.objects.select_related("qrcode", "workspace"),
        verification_code=code,
    )
    return render(request, "qrcodes/certificates/verify.html", {
        "certificate": certificate,
    })


@login_required
def qrcode_scan(request):
    return render(request, "qrcodes/scan.html", {"active_tab": "scan_qr"})


@ensure_csrf_cookie
def safe_scanner(request):
    return render(request, "scanner/index.html")


@ensure_csrf_cookie
def safe_scanner_result(request):
    return render(request, "scanner/result.html", {
        "scanner_analysis_json": json.dumps(request.session.get("safe_scanner_last_analysis")),
    })


@login_required
def safe_scanner_history(request):
    history = QRScannerHistory.objects.filter(user=request.user).order_by("-scanned_at")[:100]
    return render(request, "scanner/history.html", {"history": history})


@require_POST
def scanner_analyze_api(request):
    if _rate_limited(request, "scanner-analyze", limit=60, window=60):
        return JsonResponse({"error": "Too many scans. Please wait a moment."}, status=429)

    content = (_json_body(request).get("content") or "").strip()
    if not content:
        return JsonResponse({"error": "QR content is required."}, status=400)
    if len(content) > 4000:
        return JsonResponse({"error": "QR content is too long to inspect safely."}, status=400)

    analysis = analyze_scanner_content(content)
    analysis["verifiedBusiness"] = _verified_business_for_scanned_content(content, request)
    request.session["safe_scanner_last_analysis"] = analysis
    request.session.modified = True
    if request.user.is_authenticated:
        QRScannerHistory.objects.create(
            user=request.user,
            raw_content=analysis["rawContent"],
            content_type=analysis["type"],
            domain=analysis["domain"][:255],
            risk_level=analysis["riskLevel"],
        )
    return JsonResponse(analysis)


@require_POST
def scanner_report_api(request):
    if _rate_limited(request, "scanner-report", limit=5, window=300):
        return JsonResponse({"error": "Too many reports. Please wait before sending another."}, status=429)

    body = _json_body(request)
    content = (body.get("content") or "").strip()
    reason = (body.get("reason") or QRSuspiciousReport.Reason.OTHER).strip()
    comment = (body.get("comment") or "").strip()[:1000]
    if not content:
        return JsonResponse({"error": "Scanned QR content is required."}, status=400)
    if len(content) > 4000:
        return JsonResponse({"error": "QR content is too long to report."}, status=400)

    analysis = request.session.get("safe_scanner_last_analysis")
    if not analysis or analysis.get("rawContent") != content:
        analysis = analyze_scanner_content(content, fetch_title=False)
    report = _create_qr_report(
        request,
        content=content,
        reason=reason,
        comment=comment,
        analysis=analysis,
    )
    return JsonResponse({"ok": True, "reportId": str(report.id), "status": report.status})


@require_POST
def url_analyze_api(request):
    if _rate_limited(request, "url-analyze", limit=60, window=60):
        return JsonResponse({"error": "Too many URL checks. Please wait a moment."}, status=429)

    url = (_json_body(request).get("url") or "").strip()
    if not url:
        return JsonResponse({"error": "URL is required."}, status=400)
    if len(url) > 4000:
        return JsonResponse({"error": "URL is too long to inspect safely."}, status=400)
    return JsonResponse(analyze_url(url))


@login_required
@require_POST
def qrcode_safety_check(request):
    if _rate_limited(request, "safety-check", limit=45, window=60):
        return JsonResponse({"error": "Too many safety checks. Please wait a moment."}, status=429)
    value = (_json_body(request).get("value") or "").strip()
    if not value:
        return JsonResponse({"error": "QR value is required."}, status=400)
    if len(value) > 4000:
        return JsonResponse({"error": "QR value is too long to inspect safely."}, status=400)
    return JsonResponse(analyze_scanned_value(value))


@login_required
@require_POST
def qrcode_report_suspicious(request):
    if _rate_limited(request, "suspicious-report", limit=10, window=300):
        return JsonResponse({"error": "Too many reports. Please wait before sending another."}, status=429)

    body = _json_body(request)
    value = (body.get("value") or "").strip()
    reason = (body.get("reason") or QRSuspiciousReport.Reason.OTHER).strip()
    comment = (body.get("comment") or body.get("reason") or "").strip()[:1000]
    if not value:
        return JsonResponse({"error": "QR value is required."}, status=400)
    if len(value) > 4000:
        return JsonResponse({"error": "QR value is too long to report."}, status=400)

    analysis = analyze_scanner_content(value, fetch_title=False)
    report = _create_qr_report(
        request,
        content=value,
        reason=reason,
        comment=comment,
        analysis=analysis,
    )
    return JsonResponse({"ok": True, "report_id": str(report.id)})


@staff_member_required
def admin_reports(request):
    qs = QRSuspiciousReport.objects.select_related("qrcode", "campaign", "reporter").order_by("-created_at")
    status = request.GET.get("status", "").strip()
    reason = request.GET.get("reason", "").strip()
    risk_level = request.GET.get("risk_level", "").strip()
    date = request.GET.get("date", "").strip()
    if status:
        qs = qs.filter(status=status)
    if reason:
        qs = qs.filter(reason=reason)
    if risk_level:
        qs = qs.filter(risk_level=risk_level)
    if date:
        qs = qs.filter(created_at__date=date)

    grouped_reports = (
        QRSuspiciousReport.objects
        .values("qrcode_id", "host")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .order_by("-total")[:10]
    )
    paginator = Paginator(qs, 25)
    context = {
        "reports": paginator.get_page(request.GET.get("page", 1)),
        "status_choices": QRSuspiciousReport.Status.choices,
        "reason_choices": QRSuspiciousReport.Reason.choices,
        "risk_levels": ["safe", "caution", "risky", "warning", "danger", "unknown"],
        "grouped_reports": grouped_reports,
        "filters": {
            "status": status,
            "reason": reason,
            "risk_level": risk_level,
            "date": date,
        },
    }
    return render(request, "admin/reports.html", context)


@staff_member_required
def admin_report_detail(request, report_id):
    report = get_object_or_404(
        QRSuspiciousReport.objects.select_related("qrcode", "campaign", "reporter"),
        id=report_id,
    )
    if request.method == "POST":
        status = request.POST.get("status", report.status)
        notes = request.POST.get("admin_notes", "").strip()[:5000]
        if status in {choice[0] for choice in QRSuspiciousReport.Status.choices}:
            report.status = status
        report.admin_notes = notes
        report.save(update_fields=["status", "admin_notes", "updated_at"])
        _refresh_campaign_risk(report.campaign)
        messages.success(request, "Report updated.")
        return redirect("admin_report_detail", report_id=report.id)

    related_count = QRSuspiciousReport.objects.filter(
        Q(qrcode=report.qrcode) if report.qrcode else Q(host=report.host),
    ).count()
    return render(request, "admin/report_detail.html", {
        "report": report,
        "status_choices": QRSuspiciousReport.Status.choices,
        "related_count": related_count,
    })


@login_required
def qrcode_detail(request, pk):
    qr = get_object_or_404(QRCode, id=pk, user=request.user)

    # Analytics summary
    from apps.analytics.models import DailyQRStats
    from datetime import timedelta
    last_30_days = (timezone.now() - timedelta(days=30)).date()
    daily_stats = DailyQRStats.objects.filter(
        qrcode_id=qr.id, date__gte=last_30_days
    ).order_by("date")
    reports = QRSuspiciousReport.objects.filter(qrcode=qr).order_by("-created_at")[:10]
    open_report_count = QRSuspiciousReport.objects.filter(
        qrcode=qr,
        status__in=[QRSuspiciousReport.Status.OPEN, QRSuspiciousReport.Status.REVIEWING],
    ).count()

    context = {
        "qr": qr,
        "daily_stats": list(daily_stats.values("date", "total_scans", "unique_scans")),
        "reports": reports,
        "open_report_count": open_report_count,
        "active_tab": "qrcodes",
    }
    return render(request, "qrcodes/detail.html", context)


@login_required
@require_POST
def qrcode_pause_campaign(request, pk):
    qr = get_object_or_404(QRCode, id=pk, user=request.user)
    if not qr.campaign:
        qr.status = QRCode.Status.PAUSED
        qr.save(update_fields=["status", "updated_at"])
        messages.warning(request, f'"{qr.name}" has been paused while you review suspicious reports.')
        return redirect("qrcodes:detail", pk=qr.id)

    QRCode.objects.filter(
        user=request.user,
        campaign=qr.campaign,
        status=QRCode.Status.ACTIVE,
    ).update(status=QRCode.Status.PAUSED, updated_at=timezone.now())
    qr.campaign.is_active = False
    qr.campaign.save(update_fields=["is_active", "updated_at"])
    messages.warning(request, f'"{qr.campaign.name}" campaign has been paused temporarily.')
    return redirect("qrcodes:detail", pk=qr.id)


@login_required
def qrcode_edit(request, pk):
    qr = get_object_or_404(QRCode, id=pk, user=request.user)
    if qr.status == "deleted":
        raise Http404

    if request.method == "POST":
        form = QRCodeForm(_qrcode_form_data(request.POST), request.FILES, instance=qr, user=request.user)
        if form.is_valid():
            qr = form.save()
            if _generate_qr_now(qr):
                messages.success(request, "QR code updated and regenerated.")
            else:
                messages.warning(request, "QR code updated, but image regeneration failed. Try downloading again.")
            return redirect("qrcodes:detail", pk=qr.id)
    else:
        form = QRCodeForm(instance=qr, user=request.user)

    context = {"form": form, "qr": qr, "active_tab": "qrcodes"}
    return render(request, "qrcodes/edit.html", context)


@login_required
@require_POST
def qrcode_delete(request, pk):
    qr = get_object_or_404(QRCode, id=pk, user=request.user)
    qr.status = QRCode.Status.DELETED
    qr.save(update_fields=["status"])
    messages.success(request, f'"{qr.name}" has been deleted.')
    return redirect("qrcodes:list")


@login_required
def qrcode_download(request, pk, fmt):
    """Stream QR image as download."""
    qr = get_object_or_404(QRCode, id=pk, user=request.user)
    fmt = fmt.lower()

    if (fmt == "png" and not qr.image_png) or (fmt == "pdf" and not qr.image_pdf) or (fmt == "svg" and not qr.image_svg):
        if _generate_qr_now(qr):
            qr.refresh_from_db()

    if fmt == "png":
        if not qr.image_png:
            return HttpResponse("Image not yet generated.", status=202)
        response = HttpResponse(qr.image_png.read(), content_type="image/png")
        response["Content-Disposition"] = f'attachment; filename="{qr.short_code}.png"'
    elif fmt == "svg":
        if not qr.image_svg:
            return HttpResponse("SVG not yet generated.", status=202)
        response = HttpResponse(qr.image_svg, content_type="image/svg+xml")
        response["Content-Disposition"] = f'attachment; filename="{qr.short_code}.svg"'
    elif fmt == "pdf":
        if not qr.image_pdf:
            return HttpResponse("PDF not yet generated.", status=202)
        response = HttpResponse(qr.image_pdf.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{qr.short_code}.pdf"'
    else:
        return HttpResponse("Invalid format.", status=400)

    return response


# ── Dynamic QR redirect ──────────────────────────────────────────────────────

@never_cache
def qr_redirect(request, short_code):
    """
    Handle dynamic QR scan:
    1. Look up QR by short_code
    2. Check expiry / password
    3. Log scan event (async)
    4. Redirect to destination
    """
    qr = get_object_or_404(QRCode, short_code=short_code, status__in=["active", "paused"])

    # Check scheduled window — not yet open
    if qr.is_scheduled_inactive:
        return render(request, "qrcodes/scheduled.html", {"qr": qr}, status=200)

    # Check expiry / scan limit / scheduled end
    if qr.is_expired:
        return render(request, "qrcodes/expired.html", {"qr": qr}, status=410)

    plan_scan_limit = qr.user.plan_limits["max_scans"]
    if plan_scan_limit > 0 and qr.total_scans >= plan_scan_limit:
        return render(request, "qrcodes/expired.html", {"qr": qr}, status=410)

    # Password gate
    if qr.is_password_protected:
        if request.method == "POST":
            raw_pw = request.POST.get("password", "")
            if not qr.check_access_password(raw_pw):
                return render(request, "qrcodes/password_gate.html", {
                    "qr": qr, "error": "Incorrect password."
                })
        else:
            # Check session for already-authenticated access
            session_key = f"qr_access_{qr.short_code}"
            if not request.session.get(session_key):
                return render(request, "qrcodes/password_gate.html", {"qr": qr})
        request.session[f"qr_access_{qr.short_code}"] = True

    # Paused QR
    if qr.status == "paused":
        return render(request, "qrcodes/paused.html", {"qr": qr}, status=200)

    # Capture scan event
    ip = get_client_ip(request)
    ua = request.META.get("HTTP_USER_AGENT", "")
    referer = request.META.get("HTTP_REFERER", "")
    day_str = timezone.now().strftime("%Y-%m-%d")
    fingerprint = compute_scan_fingerprint(ip or "", ua, day_str)

    scan = QRScanEvent.objects.create(
        qrcode=qr,
        ip_address=ip,
        user_agent_raw=ua[:1000],
        fingerprint=fingerprint,
        referer=referer[:2000],
    )

    # Process immediately so local deployments without Celery still count scans.
    try:
        process_scan_event_now(str(scan.id), geolocate=False, update_aggregates=True)
    except Exception:
        logger.exception("Immediate scan processing failed for %s; queueing background retry", scan.id)
        process_scan_event.delay(str(scan.id))

    return _tracked_content_response(request, qr)


# ── Premium / utility views ──────────────────────────────────────────────────

@login_required
@require_POST
def qrcode_clone(request, pk):
    """Duplicate a QR code with the same settings (resets stats and images)."""
    if not can_create_qr(request.user):
        messages.error(request, qr_quota_message(request.user, action="clone"))
        return redirect("core:pricing")

    src = get_object_or_404(QRCode, id=pk, user=request.user)
    src.pk = None
    src.id = None
    src.short_code = ""
    src.name = f"Copy of {src.name}"
    src.total_scans = 0
    src.unique_scans = 0
    src.last_scanned_at = None
    src.image_png = None
    src.image_svg = ""
    src.image_pdf = None
    src.logo = None
    src.save()
    _generate_qr_now(src)
    messages.success(request, f'"{src.name}" cloned successfully.')
    return redirect("qrcodes:detail", pk=src.id)


@login_required
@require_POST
def qrcode_regenerate(request, pk):
    """Force-regenerate QR images (useful after plan upgrade or URL change)."""
    qr = get_object_or_404(QRCode, id=pk, user=request.user)
    qr.image_png = None
    qr.image_svg = ""
    qr.image_pdf = None
    qr.save(update_fields=["image_png", "image_svg", "image_pdf"])
    if _generate_qr_now(qr):
        messages.success(request, "QR images regenerated.")
    else:
        messages.error(request, "Image generation failed — check logs.")
    return redirect("qrcodes:detail", pk=qr.id)


@login_required
def qrcode_export_zip(request):
    """Bulk-download all (or selected) QR codes as a ZIP of PNG files."""
    if not request.user.plan_limits["export"]:
        messages.error(request, "ZIP export requires a Pro plan.")
        return redirect("qrcodes:list")

    ids = request.GET.getlist("ids")
    qs = QRCode.objects.filter(user=request.user).exclude(status="deleted")
    if ids:
        qs = qs.filter(id__in=ids)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for qr in qs:
            if not qr.image_png:
                _generate_qr_now(qr)
                qr.refresh_from_db(fields=["image_png"])
            if qr.image_png:
                try:
                    zf.writestr(f"{qr.name}_{qr.short_code}.png", qr.image_png.read())
                except Exception as exc:
                    logger.warning("ZIP: skipping %s — %s", qr.short_code, exc)

    buf.seek(0)
    response = HttpResponse(buf.read(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="qrcodes_export.zip"'
    return response


@login_required
def qrcode_analytics_csv(request, pk):
    """Export per-QR daily analytics as CSV (pro feature)."""
    if not request.user.plan_limits["export"]:
        messages.error(request, "CSV export requires a Pro plan.")
        return redirect("qrcodes:detail", pk=pk)

    from apps.analytics.models import DailyQRStats
    qr = get_object_or_404(QRCode, id=pk, user=request.user)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="analytics_{qr.short_code}.csv"'

    writer = csv.writer(response)
    writer.writerow(["date", "total_scans", "unique_scans", "mobile", "desktop", "tablet",
                     "top_countries", "top_browsers"])
    for s in DailyQRStats.objects.filter(qrcode_id=qr.id).order_by("date"):
        writer.writerow([
            s.date, s.total_scans, s.unique_scans,
            s.mobile_scans, s.desktop_scans, s.tablet_scans,
            str(s.country_breakdown), str(s.browser_breakdown),
        ])
    return response


@login_required
def premium_studio(request):
    if not request.user.is_pro:
        messages.error(request, "Premium Studio requires a paid membership.")
        return redirect("core:pricing")

    qrcodes = QRCode.objects.filter(user=request.user).exclude(status="deleted").order_by("-updated_at")[:12]
    health_summary = QRHealthCheck.objects.filter(qrcode__user=request.user)
    context = {
        "active_tab": "premium",
        "qrcodes": qrcodes,
        "broken_count": health_summary.filter(status="broken").count(),
        "warning_count": health_summary.filter(status="warning").count(),
        "conversion_count": QRConversionEvent.objects.filter(qrcode__user=request.user).count(),
        "rule_count": QRDestinationRule.objects.filter(qrcode__user=request.user, is_active=True).count(),
    }
    return render(request, "qrcodes/premium.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def qrcode_preflight(request, pk):
    if not request.user.is_pro:
        messages.error(request, "Preflight, smart routing, and landing pages require a paid membership.")
        return redirect("core:pricing")

    qr = get_object_or_404(QRCode, id=pk, user=request.user)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "health":
            destination = (qr.destination_url if qr.is_dynamic else qr.content) or ""
            result = assess_destination(destination)
            result["checked_at"] = timezone.now()
            QRHealthCheck.objects.update_or_create(qrcode=qr, defaults=result)
            messages.success(request, "Destination health check completed.")
            return redirect("qrcodes:preflight", pk=qr.id)
        if action == "rule":
            name = request.POST.get("name", "").strip() or "Smart rule"
            destination_url = request.POST.get("destination_url", "").strip()
            rule_type = request.POST.get("rule_type", QRDestinationRule.RuleType.DEVICE)
            if destination_url:
                QRDestinationRule.objects.create(
                    qrcode=qr,
                    name=name[:120],
                    rule_type=rule_type,
                    match_value=request.POST.get("match_value", "").strip()[:120],
                    destination_url=destination_url,
                    weight=max(int(request.POST.get("weight") or 50), 1),
                )
                messages.success(request, "Smart destination rule added.")
            else:
                messages.error(request, "Destination URL is required for a rule.")
            return redirect("qrcodes:preflight", pk=qr.id)
        if action == "landing":
            QRLandingPage.objects.update_or_create(
                qrcode=qr,
                defaults={
                    "mode": request.POST.get("mode") or QRLandingPage.Mode.LANDING,
                    "title": request.POST.get("title", "").strip() or qr.name,
                    "body": request.POST.get("body", "").strip(),
                    "primary_label": request.POST.get("primary_label", "").strip() or "Open",
                    "primary_url": request.POST.get("primary_url", "").strip(),
                    "published": True,
                },
            )
            messages.success(request, "Built-in landing page saved.")
            return redirect("qrcodes:preflight", pk=qr.id)

    try:
        health = qr.health_check
    except QRHealthCheck.DoesNotExist:
        health = None
    try:
        landing = qr.landing_page
    except QRLandingPage.DoesNotExist:
        landing = None
    context = {
        "active_tab": "qrcodes",
        "qr": qr,
        "health": health,
        "preprint": build_preprint_check(qr, health),
        "rules": qr.destination_rules.all(),
        "landing": landing,
        "landing_url": request.build_absolute_uri(f"/p/{qr.short_code}/"),
        "conversion_url": request.build_absolute_uri(f"/c/{qr.short_code}/click/"),
    }
    return render(request, "qrcodes/preflight.html", context)


def public_landing_page(request, short_code):
    qr = get_object_or_404(QRCode, short_code=short_code, status=QRCode.Status.ACTIVE)
    landing = get_object_or_404(QRLandingPage, qrcode=qr, published=True)
    return render(request, "qrcodes/public_landing.html", {"qr": qr, "landing": landing})


def conversion_redirect(request, short_code, event_type):
    qr = get_object_or_404(QRCode, short_code=short_code)
    QRConversionEvent.objects.create(
        qrcode=qr,
        event_type=event_type[:60],
        metadata={
            "referer": request.META.get("HTTP_REFERER", "")[:500],
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
        },
    )
    try:
        landing = qr.landing_page
    except QRLandingPage.DoesNotExist:
        landing = None
    next_url = request.GET.get("next") or (landing.primary_url if landing else "")
    if next_url and urlparse(next_url).scheme in ("http", "https"):
        return redirect(next_url)
    return redirect(f"/p/{short_code}/")
