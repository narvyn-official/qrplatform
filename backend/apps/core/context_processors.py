"""
Template context processors — inject platform-wide settings into all templates.
"""
from django.conf import settings


PRIVATE_PATH_PREFIXES = (
    "/accounts/",
    "/admin/",
    "/api/",
    "/dashboard/",
    "/__debug__/",
)


def platform_settings(request):
    canonical_url = request.build_absolute_uri(request.path)
    seo_description = (
        "Create dynamic QR codes and barcodes, update destinations after printing, "
        "track scans with analytics, and export production-ready campaign assets."
    )
    robots = "noindex, nofollow" if request.path.startswith(PRIVATE_PATH_PREFIXES) else "index, follow"

    return {
        "PLATFORM_NAME": settings.PLATFORM_NAME,
        "PLATFORM_URL": settings.PLATFORM_URL,
        "PLATFORM_DOMAIN": settings.PLATFORM_DOMAIN,
        "CANONICAL_URL": canonical_url,
        "SEO_DEFAULT_DESCRIPTION": seo_description,
        "SEO_DEFAULT_IMAGE": f"{settings.PLATFORM_URL.rstrip('/')}/static/pwa/icon-512.png",
        "SEO_ROBOTS": robots,
    }
