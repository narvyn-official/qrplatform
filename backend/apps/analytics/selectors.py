"""Read-side analytics helpers used by dashboards and APIs."""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.analytics.models import UserDailyStats
from apps.qrcodes.models import QRScanEvent


def account_daily_scan_series(user, days=7):
    """Return a gap-filled recent scan series.

    Prefer account-level daily aggregates for production dashboards and fall
    back to raw events for dates that have not been aggregated yet. This keeps
    test/dev behavior and near-real-time dashboards intact while avoiding a raw
    scan-event sweep on every request once aggregates exist.
    """
    days = max(int(days or 1), 1)
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=days - 1)
    dates = [start_date + timedelta(days=offset) for offset in range(days)]

    by_date = {
        row["date"]: {
            "total_scans": row["total_scans"],
            "unique_scans": row["unique_scans"],
        }
        for row in UserDailyStats.objects.filter(
            user_id=user.id,
            date__gte=start_date,
            date__lte=end_date,
        ).values("date", "total_scans", "unique_scans")
    }

    missing_dates = [day for day in dates if day not in by_date]
    if missing_dates:
        rows = (
            QRScanEvent.objects.filter(
                qrcode__user=user,
                timestamp__date__in=missing_dates,
                is_processed=True,
            )
            .exclude(device_type="bot")
            .values("timestamp__date")
            .annotate(
                total_scans=Count("id"),
                unique_scans=Count("id", filter=Q(is_unique=True)),
            )
        )
        by_date.update({
            row["timestamp__date"]: {
                "total_scans": row["total_scans"],
                "unique_scans": row["unique_scans"],
            }
            for row in rows
        })

    return [
        {
            "date": day,
            "total_scans": by_date.get(day, {}).get("total_scans", 0),
            "unique_scans": by_date.get(day, {}).get("unique_scans", 0),
        }
        for day in dates
    ]
