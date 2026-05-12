"""
Celery application configuration.
"""
import os
from celery import Celery
from celery.signals import setup_logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qrplatform.settings.production")

app = Celery("qrplatform")

app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks from all registered Django apps
app.autodiscover_tasks()


@setup_logging.connect
def config_loggers(*args, **kwargs):
    from logging.config import dictConfig
    from django.conf import settings
    dictConfig(settings.LOGGING)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")


# Periodic tasks (defined here + managed via django-celery-beat in DB)
app.conf.beat_schedule = {
    "cleanup-expired-qrcodes": {
        "task": "apps.qrcodes.tasks.cleanup_expired_qrcodes",
        "schedule": 3600.0,  # every hour
    },
    "aggregate-daily-analytics": {
        "task": "apps.analytics.tasks.aggregate_daily_analytics",
        "schedule": 86400.0,  # daily
    },
    "process-pending-scan-events": {
        "task": "apps.analytics.tasks.process_pending_scan_events",
        "schedule": 300.0,  # every 5 minutes
    },
    "cleanup-old-scan-events": {
        "task": "apps.analytics.tasks.cleanup_old_raw_events",
        "schedule": 86400.0,
    },
    "send-weekly-reports": {
        "task": "apps.analytics.tasks.send_weekly_reports",
        "schedule": 604800.0,  # weekly
    },
}
