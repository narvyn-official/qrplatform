"""
Analytics aggregation models — pre-aggregated for fast dashboard queries.
"""
import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class DailyQRStats(models.Model):
    """Pre-aggregated daily stats per QR code. Updated by Celery task."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qrcode_id = models.UUIDField(db_index=True)
    user_id = models.UUIDField(db_index=True)
    date = models.DateField(db_index=True)

    total_scans = models.PositiveIntegerField(default=0)
    unique_scans = models.PositiveIntegerField(default=0)
    mobile_scans = models.PositiveIntegerField(default=0)
    desktop_scans = models.PositiveIntegerField(default=0)
    tablet_scans = models.PositiveIntegerField(default=0)

    # Top countries (JSON: {"US": 120, "GB": 45, ...})
    country_breakdown = models.JSONField(default=dict)
    # Top browsers
    browser_breakdown = models.JSONField(default=dict)
    # Top OS
    os_breakdown = models.JSONField(default=dict)

    class Meta:
        db_table = "analytics_daily_qr_stats"
        unique_together = [("qrcode_id", "date")]
        indexes = [
            models.Index(fields=["qrcode_id", "date"]),
            models.Index(fields=["user_id", "date"]),
            models.Index(fields=["date"]),
        ]

    def __str__(self):
        return f"DailyStats({self.qrcode_id} — {self.date})"


class HourlyQRStats(models.Model):
    """Hourly breakdown for heatmap visualization."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qrcode_id = models.UUIDField(db_index=True)
    date = models.DateField()
    hour = models.PositiveSmallIntegerField()  # 0–23
    scans = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "analytics_hourly_qr_stats"
        unique_together = [("qrcode_id", "date", "hour")]
        indexes = [models.Index(fields=["qrcode_id", "date"])]

    def __str__(self):
        return f"HourlyStat({self.qrcode_id} {self.date} {self.hour}:00)"


class GeoStats(models.Model):
    """Geographic aggregation per QR code."""

    qrcode_id = models.UUIDField(db_index=True)
    country_code = models.CharField(max_length=2)
    country_name = models.CharField(max_length=100)
    city = models.CharField(max_length=100, blank=True)
    scans = models.PositiveIntegerField(default=0)
    last_scan_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "analytics_geo_stats"
        unique_together = [("qrcode_id", "country_code", "city")]
        indexes = [
            models.Index(fields=["qrcode_id"]),
            models.Index(fields=["country_code"]),
        ]

    def __str__(self):
        return f"GeoStat({self.qrcode_id} — {self.country_name})"


class UserDailyStats(models.Model):
    """Aggregated daily totals for a user's account (for main dashboard)."""

    user_id = models.UUIDField(db_index=True)
    date = models.DateField(db_index=True)
    total_scans = models.PositiveIntegerField(default=0)
    unique_scans = models.PositiveIntegerField(default=0)
    active_qr_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "analytics_user_daily_stats"
        unique_together = [("user_id", "date")]
        indexes = [models.Index(fields=["user_id", "date"])]

    def __str__(self):
        return f"UserDailyStat({self.user_id} — {self.date})"
