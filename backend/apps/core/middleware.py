"""
Custom middleware — request logging, timezone, security headers.
"""
import logging
import time
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    """Log every request with timing for performance monitoring."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        duration_ms = (time.monotonic() - start) * 1000

        if not request.path.startswith("/static/") and not request.path.startswith("/media/"):
            logger.info(
                "%s %s %d %.1fms",
                request.method,
                request.path,
                response.status_code,
                duration_ms,
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": round(duration_ms, 1),
                    "user": str(getattr(request, "user", "anon")),
                },
            )

        return response


class TimezoneMiddleware:
    """Activate the user's profile timezone for the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                import zoneinfo
                tz_name = request.user.profile.timezone
                request_tz = zoneinfo.ZoneInfo(tz_name)
                timezone.activate(request_tz)
            except Exception:
                timezone.deactivate()
        else:
            timezone.deactivate()

        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Add additional security headers not covered by Django defaults."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response["Cross-Origin-Opener-Policy"] = "same-origin"
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        return response
