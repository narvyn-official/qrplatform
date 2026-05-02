import logging
from datetime import timedelta
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone

from apps.qrcodes.models import QRCode
from apps.analytics.models import DailyQRStats, GeoStats, HourlyQRStats

logger = logging.getLogger(__name__)


@login_required
def analytics_detail(request, qr_id):
    qr = get_object_or_404(QRCode, id=qr_id, user=request.user)
    days = int(request.GET.get("days", 30))
    cutoff = (timezone.now() - timedelta(days=days)).date()

    daily_stats = DailyQRStats.objects.filter(
        qrcode_id=qr.id, date__gte=cutoff
    ).order_by("date")

    geo_stats = GeoStats.objects.filter(
        qrcode_id=qr.id
    ).order_by("-scans")[:20]

    hourly_stats = HourlyQRStats.objects.filter(
        qrcode_id=qr.id, date__gte=cutoff
    ).values("hour").order_by("hour")

    # Build heatmap data (day_of_week x hour)
    heatmap = {}
    for stat in HourlyQRStats.objects.filter(
        qrcode_id=qr.id, date__gte=cutoff
    ):
        day = stat.date.weekday()
        key = f"{day}_{stat.hour}"
        heatmap[key] = heatmap.get(key, 0) + stat.scans

    context = {
        "qr": qr,
        "daily_stats": list(daily_stats.values("date", "total_scans", "unique_scans",
                                                "mobile_scans", "desktop_scans")),
        "geo_stats": list(geo_stats.values("country_code", "country_name", "city", "scans")),
        "heatmap_data": heatmap,
        "days": days,
        "active_tab": "analytics",
    }
    return render(request, "analytics/detail.html", context)
