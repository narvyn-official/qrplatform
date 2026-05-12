"""Read-side analytics helpers used by dashboards and APIs."""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.qrcodes.models import QRScanEvent


def account_daily_scan_series(user, days=7):
    """Return a gap-filled recent scan series from processed raw events."""
    days = max(int(days or 1), 1)
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=days - 1)

    rows = (
        QRScanEvent.objects.filter(
            qrcode__user=user,
            timestamp__date__gte=start_date,
            timestamp__date__lte=end_date,
            is_processed=True,
        )
        .exclude(device_type="bot")
        .values("timestamp__date")
        .annotate(
            total_scans=Count("id"),
            unique_scans=Count("id", filter=Q(is_unique=True)),
        )
    )
    by_date = {
        row["timestamp__date"]: {
            "total_scans": row["total_scans"],
            "unique_scans": row["unique_scans"],
        }
        for row in rows
    }

    return [
        {
            "date": start_date + timedelta(days=offset),
            "total_scans": by_date.get(start_date + timedelta(days=offset), {}).get("total_scans", 0),
            "unique_scans": by_date.get(start_date + timedelta(days=offset), {}).get("unique_scans", 0),
        }
        for offset in range(days)
    ]
