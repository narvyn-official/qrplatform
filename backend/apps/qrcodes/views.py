"""
QR Code views — dashboard CRUD and dynamic redirect handler.
"""
import io
import csv
import logging
import zipfile
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import (
    HttpResponse, JsonResponse, Http404, HttpResponseForbidden
)
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.cache import never_cache
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.utils import timezone
from django.conf import settings

from apps.qrcodes.models import QRCode, QRScanEvent, QRCodeCampaign
from apps.qrcodes.forms import QRCodeForm
from apps.qrcodes.tasks import generate_qr_images
from apps.analytics.utils import get_client_ip, compute_scan_fingerprint
from apps.analytics.tasks import process_scan_event

logger = logging.getLogger(__name__)


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


# ── Dashboard views ──────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    user = request.user
    qrcodes = QRCode.objects.filter(user=user, status__in=["active", "paused"]).order_by("-created_at")

    total_qr = qrcodes.count()
    total_scans = qrcodes.aggregate(s=Sum("total_scans"))["s"] or 0

    # Last 7 days stats
    from datetime import date, timedelta
    from apps.analytics.models import UserDailyStats
    week_ago = (timezone.now() - timedelta(days=7)).date()
    weekly_stats = UserDailyStats.objects.filter(
        user_id=user.id, date__gte=week_ago
    ).order_by("date")

    # Recent QR codes
    recent_qrcodes = qrcodes[:5]

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
        "weekly_stats": list(weekly_stats.values("date", "total_scans", "unique_scans")),
        "recent_qrcodes": recent_qrcodes,
        "quick_actions": quick_actions,
        "active_tab": "dashboard",
    }
    return render(request, "dashboard/index.html", context)


@login_required
def qrcode_list(request):
    user = request.user
    qs = QRCode.objects.filter(user=user).exclude(status="deleted")

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
def qrcode_scan(request):
    return render(request, "qrcodes/scan.html", {"active_tab": "scan_qr"})


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

    context = {
        "qr": qr,
        "daily_stats": list(daily_stats.values("date", "total_scans", "unique_scans")),
        "active_tab": "qrcodes",
    }
    return render(request, "qrcodes/detail.html", context)


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

    # Process asynchronously (geolocation, dedup, aggregation)
    process_scan_event.delay(str(scan.id))

    # Resolve destination (dynamic uses destination_url; URL/static uses content)
    destination = (qr.destination_url if qr.is_dynamic else qr.content) or ""
    if not destination:
        return render(request, "qrcodes/expired.html", {"qr": qr}, status=410)

    # Apply UTM auto-append (pro feature)
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


# ── Premium / utility views ──────────────────────────────────────────────────

@login_required
@require_POST
def qrcode_clone(request, pk):
    """Duplicate a QR code with the same settings (resets stats and images)."""
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
