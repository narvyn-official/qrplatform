"""
Template context processors — inject platform-wide settings into all templates.
"""
from django.conf import settings


def platform_settings(request):
    return {
        "PLATFORM_NAME": settings.PLATFORM_NAME,
        "PLATFORM_URL": settings.PLATFORM_URL,
        "PLATFORM_DOMAIN": settings.PLATFORM_DOMAIN,
    }
