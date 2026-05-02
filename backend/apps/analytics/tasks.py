"""
Celery tasks for asynchronous analytics processing.
"""
import logging
from datetime import date, timedelta
from collections import defaultdict

from celery import shared_task
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.qrcodes.models import QRScanEvent, QRCode
from apps.analytics.models import DailyQRStats, HourlyQRStats, GeoStats, UserDailyStats
from apps.analytics.utils import parse_user_agent, geolocate_ip, compute_scan_fingerprint

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_scan_event(self, scan_event_id: str):
    """
    Process a raw scan event:
    - Parse user agent
    - Geolocate IP
    - Set deduplication flag
    - Update QR counters
    """
    try:
        event = QRScanEvent.objects.select_related("qrcode").get(id=scan_event_id)
    except QRScanEvent.DoesNotExist:
        logger.error("ScanEvent %s not found", scan_event_id)
        return

    if event.is_processed:
        return

    try:
        # 1. Parse user agent
        ua_info = parse_user_agent(event.user_agent_raw)
        event.device_type = ua_info["device_type"]
        event.os_family = ua_info["os_family"]
        event.browser_family = ua_info["browser_family"]

        # 2. Skip bot scans (don't count, don't geolocate)
        if event.device_type == "bot":
            event.is_processed = True
            event.save(update_fields=["device_type", "os_family", "browser_family", "is_processed"])
            return

        # 3. Geolocate
        if event.ip_address:
            geo = geolocate_ip(event.ip_address)
            event.country_code = geo["country_code"]
            event.country_name = geo["country_name"]
            event.region = geo["region"]
            event.city = geo["city"]
            event.latitude = geo["latitude"]
            event.longitude = geo["longitude"]

        # 4. Deduplication fingerprint
        day_str = event.timestamp.strftime("%Y-%m-%d")
        fingerprint = compute_scan_fingerprint(
            event.ip_address or "", event.user_agent_raw, day_str
        )
        is_unique = not QRScanEvent.objects.filter(
            qrcode=event.qrcode,
            fingerprint=fingerprint,
            is_unique=True,
        ).exists()
        event.fingerprint = fingerprint
        event.is_unique = is_unique
        event.is_processed = True

        with transaction.atomic():
            event.save()
            event.qrcode.increment_scan(is_unique=is_unique)

        # 5. Update aggregated stats asynchronously
        update_daily_stats.delay(str(event.qrcode.id), day_str)

    except Exception as exc:
        logger.exception("Error processing scan %s: %s", scan_event_id, exc)
        raise self.retry(exc=exc)


@shared_task
def update_daily_stats(qrcode_id: str, date_str: str):
    """Recompute DailyQRStats for a given QR + date from raw events."""
    from apps.qrcodes.models import QRCode as QR

    try:
        qr = QR.objects.get(id=qrcode_id)
    except QR.DoesNotExist:
        return

    target_date = date.fromisoformat(date_str)
    events = QRScanEvent.objects.filter(
        qrcode_id=qrcode_id,
        timestamp__date=target_date,
        is_processed=True,
    ).exclude(device_type="bot")

    total = events.count()
    unique = events.filter(is_unique=True).count()

    device_counts = events.values("device_type").annotate(c=Count("id"))
    mobile = desktop = tablet = 0
    for row in device_counts:
        if row["device_type"] == "mobile":
            mobile = row["c"]
        elif row["device_type"] == "desktop":
            desktop = row["c"]
        elif row["device_type"] == "tablet":
            tablet = row["c"]

    country_data = {}
    for row in events.values("country_code").annotate(c=Count("id")):
        if row["country_code"]:
            country_data[row["country_code"]] = row["c"]

    browser_data = {}
    for row in events.values("browser_family").annotate(c=Count("id")):
        if row["browser_family"]:
            browser_data[row["browser_family"]] = row["c"]

    os_data = {}
    for row in events.values("os_family").annotate(c=Count("id")):
        if row["os_family"]:
            os_data[row["os_family"]] = row["c"]

    DailyQRStats.objects.update_or_create(
        qrcode_id=qrcode_id,
        date=target_date,
        defaults={
            "user_id": str(qr.user_id),
            "total_scans": total,
            "unique_scans": unique,
            "mobile_scans": mobile,
            "desktop_scans": desktop,
            "tablet_scans": tablet,
            "country_breakdown": country_data,
            "browser_breakdown": browser_data,
            "os_breakdown": os_data,
        },
    )

    # Update hourly breakdown
    for hour in range(24):
        count = events.filter(timestamp__hour=hour).count()
        if count > 0:
            HourlyQRStats.objects.update_or_create(
                qrcode_id=qrcode_id,
                date=target_date,
                hour=hour,
                defaults={"scans": count},
            )

    # Update GeoStats
    for row in events.values("country_code", "country_name", "city").annotate(c=Count("id")):
        if row["country_code"]:
            GeoStats.objects.update_or_create(
                qrcode_id=qrcode_id,
                country_code=row["country_code"],
                city=row["city"] or "",
                defaults={
                    "country_name": row["country_name"] or "",
                    "scans": row["c"],
                    "last_scan_at": timezone.now(),
                },
            )

    # Update user daily stats
    _update_user_daily_stats(str(qr.user_id), date_str)


def _update_user_daily_stats(user_id: str, date_str: str):
    target_date = date.fromisoformat(date_str)
    from apps.qrcodes.models import QRCode as QR

    user_events = QRScanEvent.objects.filter(
        qrcode__user_id=user_id,
        timestamp__date=target_date,
        is_processed=True,
    ).exclude(device_type="bot")

    total = user_events.count()
    unique = user_events.filter(is_unique=True).count()
    active_qr = QR.objects.filter(user_id=user_id, status="active").count()

    UserDailyStats.objects.update_or_create(
        user_id=user_id,
        date=target_date,
        defaults={
            "total_scans": total,
            "unique_scans": unique,
            "active_qr_count": active_qr,
        },
    )


@shared_task
def aggregate_daily_analytics():
    """Nightly job to ensure all yesterday's stats are aggregated."""
    yesterday = (timezone.now() - timedelta(days=1)).date()
    date_str = yesterday.isoformat()

    unprocessed_qr_ids = (
        QRScanEvent.objects.filter(timestamp__date=yesterday, is_processed=True)
        .values_list("qrcode_id", flat=True)
        .distinct()
    )

    for qrcode_id in unprocessed_qr_ids:
        update_daily_stats.delay(str(qrcode_id), date_str)

    logger.info("Queued daily aggregation for %d QR codes", len(unprocessed_qr_ids))


@shared_task
def cleanup_old_raw_events():
    """Delete raw scan events older than 90 days to save storage (stats remain aggregated)."""
    cutoff = timezone.now() - timedelta(days=90)
    deleted, _ = QRScanEvent.objects.filter(timestamp__lt=cutoff, is_processed=True).delete()
    logger.info("Cleaned up %d old scan events", deleted)


@shared_task
def send_weekly_reports():
    """Send weekly analytics emails to users who opted in."""
    from django.contrib.auth import get_user_model
    User = get_user_model()

    users = User.objects.filter(
        is_active=True,
        profile__email_weekly_report=True,
    ).select_related("profile")

    for user in users:
        try:
            _send_weekly_report_email(user)
        except Exception as exc:
            logger.error("Failed to send weekly report to %s: %s", user.email, exc)


def _send_weekly_report_email(user):
    from django.conf import settings
    from django.template.loader import render_to_string

    week_ago = (timezone.now() - timedelta(days=7)).date()
    stats = UserDailyStats.objects.filter(user_id=user.id, date__gte=week_ago)

    total_scans = sum(s.total_scans for s in stats)
    unique_scans = sum(s.unique_scans for s in stats)

    context = {
        "user": user,
        "total_scans": total_scans,
        "unique_scans": unique_scans,
        "platform_name": settings.PLATFORM_NAME,
        "platform_url": settings.PLATFORM_URL,
    }

    html_body = render_to_string("emails/weekly_report.html", context)
    text_body = render_to_string("emails/weekly_report.txt", context)

    from django.core.mail import EmailMultiAlternatives
    msg = EmailMultiAlternatives(
        subject=f"Your weekly {settings.PLATFORM_NAME} report",
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send()
