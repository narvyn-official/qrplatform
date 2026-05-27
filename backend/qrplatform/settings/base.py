"""
Base settings for QR Platform — shared across all environments.
"""
import os
from pathlib import Path
from datetime import timedelta
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config("SECRET_KEY")

DEBUG = config("DEBUG", default=False, cast=bool)

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

# ── Application definition ───────────────────────────────────────────────────

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "axes",
    "django_celery_beat",
    "django_celery_results",
    "drf_spectacular",
    "storages",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.qrcodes",
    "apps.barcodes",
    "apps.analytics",
    "apps.api",
    "apps.platform_admin",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
    "apps.core.middleware.RequestLoggingMiddleware",
    "apps.core.middleware.TimezoneMiddleware",
]

ROOT_URLCONF = "qrplatform.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.platform_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "qrplatform.wsgi.application"

# ── Database ─────────────────────────────────────────────────────────────────

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME", default="qrplatform"),
        "USER": config("DB_USER", default="qrplatform"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {
            "connect_timeout": 10,
        },
    }
}

# ── Authentication ────────────────────────────────────────────────────────────

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/"
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"

# ── REST Framework ────────────────────────────────────────────────────────────

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/hour",
        "user": "1000/day",
        "qr_generation": "100/hour",
        "api_key": "10000/day",
    },
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardResultsPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.custom_exception_handler",
}

# ── JWT Settings ──────────────────────────────────────────────────────────────

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": config("JWT_SECRET_KEY", default=SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_OBTAIN_SERIALIZER": "apps.accounts.serializers.CustomTokenObtainPairSerializer",
}

# ── Redis ─────────────────────────────────────────────────────────────────────

REDIS_URL = config("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SOCKET_CONNECT_TIMEOUT": 5,
            "SOCKET_TIMEOUT": 5,
            "RETRY_ON_TIMEOUT": True,
            "MAX_CONNECTIONS": 1000,
            "COMPRESSOR": "django_redis.compressors.zlib.ZlibCompressor",
        },
        "KEY_PREFIX": "qrplatform",
        "TIMEOUT": 300,
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# ── Celery ────────────────────────────────────────────────────────────────────

CELERY_BROKER_URL = config("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKS_LATE = True
CELERY_BEAT_SCHEDULE = {
    "aggregate-daily-analytics": {
        "task": "apps.analytics.tasks.aggregate_daily_analytics",
        "schedule": 3600,  # every hour
    },
    "check-scheduled-qr-codes": {
        "task": "apps.analytics.tasks.check_scheduled_qr_codes",
        "schedule": 300,  # every 5 minutes
    },
    "send-weekly-reports": {
        "task": "apps.analytics.tasks.send_weekly_reports",
        "schedule": 604800,  # weekly
    },
    "cleanup-old-raw-events": {
        "task": "apps.analytics.tasks.cleanup_old_raw_events",
        "schedule": 86400,  # daily
    },
}

# ── Email ─────────────────────────────────────────────────────────────────────

EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = config("EMAIL_HOST", default="smtp.sendgrid.net")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="noreply@narvyn.com")
SERVER_EMAIL = config("SERVER_EMAIL", default="errors@narvyn.com")
EMAIL_VERIFICATION_REQUIRED = config("EMAIL_VERIFICATION_REQUIRED", default=False, cast=bool)

# Google OAuth sign-in. Add the callback URL in Google Cloud Console:
# {PLATFORM_URL}/accounts/google/callback/
GOOGLE_OAUTH_CLIENT_ID = config("GOOGLE_OAUTH_CLIENT_ID", default="")
GOOGLE_OAUTH_CLIENT_SECRET = config("GOOGLE_OAUTH_CLIENT_SECRET", default="")

# ── Static & Media ────────────────────────────────────────────────────────────

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ── Internationalization ──────────────────────────────────────────────────────

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Security Headers ──────────────────────────────────────────────────────────

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# ── CORS ──────────────────────────────────────────────────────────────────────

CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="http://localhost:3000", cast=Csv())
CORS_ALLOW_CREDENTIALS = True

# ── Axes (Brute-force protection) ─────────────────────────────────────────────

AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=30)
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ADMIN = True
AXES_LOCKOUT_CALLABLE = "apps.accounts.views.axes_lockout_handler"

# ── GeoIP ─────────────────────────────────────────────────────────────────────

GEOIP_PATH = config("GEOIP_PATH", default=str(BASE_DIR / "geoip"))
GEOIP_COUNTRY = "GeoLite2-Country.mmdb"
GEOIP_CITY = "GeoLite2-City.mmdb"

# ── Platform Settings ─────────────────────────────────────────────────────────

PLATFORM_NAME = config("PLATFORM_NAME", default="Narvyn QR")
PLATFORM_DOMAIN = config("PLATFORM_DOMAIN", default="qr.narvyn.com")
PLATFORM_URL = config("PLATFORM_URL", default="https://qr.narvyn.com")
QR_REDIRECT_BASE = config("QR_REDIRECT_BASE", default="https://qr.narvyn.com/r/")
SHORT_URL_BASE = config("SHORT_URL_BASE", default="https://qr.narvyn.com/r/")

# Payment gateway. Supported values: "paytm", "cashfree".
PAYMENT_GATEWAY = config("PAYMENT_GATEWAY", default="paytm")

# Paytm payment gateway (UPI, Paytm wallet, cards, netbanking through JS Checkout).
PAYTM_ENVIRONMENT = config("PAYTM_ENVIRONMENT", default="staging")
PAYTM_MID = config("PAYTM_MID", default="")
PAYTM_MERCHANT_KEY = config("PAYTM_MERCHANT_KEY", default="")
PAYTM_WEBSITE_NAME = config(
    "PAYTM_WEBSITE_NAME",
    default="DEFAULT" if PAYTM_ENVIRONMENT == "production" else "WEBSTAGING",
)

# Cashfree payment gateway (hosted checkout).
CASHFREE_ENVIRONMENT = config("CASHFREE_ENVIRONMENT", default="sandbox")
CASHFREE_CLIENT_ID = config("CASHFREE_CLIENT_ID", default="")
CASHFREE_CLIENT_SECRET = config("CASHFREE_CLIENT_SECRET", default="")
CASHFREE_API_VERSION = config("CASHFREE_API_VERSION", default="2025-01-01")
CASHFREE_PAYMENT_METHODS = config("CASHFREE_PAYMENT_METHODS", default="upi")
CASHFREE_DEFAULT_CUSTOMER_PHONE = config("CASHFREE_DEFAULT_CUSTOMER_PHONE", default="9999999999")

# Free tier limits
FREE_TIER_QR_LIMIT = 5
FREE_TIER_SCAN_LIMIT = 500
FREE_TIER_BARCODE_LIMIT = 10

# Pro tier limits
PRO_TIER_QR_LIMIT = 500
PRO_TIER_SCAN_LIMIT = 50000

# ── API Docs ──────────────────────────────────────────────────────────────────

SPECTACULAR_SETTINGS = {
    "TITLE": "Narvyn QR API",
    "DESCRIPTION": "Trusted QR code, safe scanner, barcode, certificate, and campaign analytics platform API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "TAGS": [
        {"name": "auth", "description": "Authentication endpoints"},
        {"name": "qrcodes", "description": "QR Code management"},
        {"name": "barcodes", "description": "Barcode management"},
        {"name": "analytics", "description": "Analytics & tracking"},
        {"name": "campaigns", "description": "Campaign management"},
    ],
}

# ── Logging ───────────────────────────────────────────────────────────────────

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {"format": "{levelname} {message}", "style": "{"},
        "json": {"()": "apps.core.logging.JsonFormatter"},
    },
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
        "require_debug_true": {"()": "django.utils.log.RequireDebugTrue"},
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "level": "WARNING",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": BASE_DIR / "logs" / "qrplatform.log",
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 10,
            "formatter": "json",
        },
        "security_file": {
            "level": "INFO",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": BASE_DIR / "logs" / "security.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 10,
            "formatter": "json",
        },
        "mail_admins": {
            "level": "ERROR",
            "filters": ["require_debug_false"],
            "class": "django.utils.log.AdminEmailHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        "django.security": {"handlers": ["security_file"], "level": "INFO", "propagate": False},
        "apps": {"handlers": ["console", "file"], "level": "DEBUG", "propagate": False},
        "celery": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
    },
}

# Create logs directory
os.makedirs(BASE_DIR / "logs", exist_ok=True)
