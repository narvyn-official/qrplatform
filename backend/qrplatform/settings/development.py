"""
Development settings — verbose, debug-friendly.
"""
from .base import *  # noqa

DEBUG = True

INSTALLED_APPS += ["debug_toolbar"]  # noqa

MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware"] + MIDDLEWARE  # noqa

INTERNAL_IPS = ["127.0.0.1"]

# Use console email in development
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Keep locally generated dynamic QR codes on the local development server.
# The root .env may contain production domains for Docker/production, so dev
# uses separate optional overrides instead of QR_REDIRECT_BASE/PLATFORM_URL.
PLATFORM_URL = config("DEV_PLATFORM_URL", default="http://127.0.0.1:8000")
QR_REDIRECT_BASE = config("DEV_QR_REDIRECT_BASE", default=f"{PLATFORM_URL}/r/")
SHORT_URL_BASE = config("DEV_SHORT_URL_BASE", default=QR_REDIRECT_BASE)

# Run Celery tasks synchronously in dev — no worker needed
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Relax throttling during development
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa
    "anon": "1000/hour",
    "user": "100000/day",
    "qr_generation": "1000/hour",
    "api_key": "100000/day",
}
