"""
Template context processors — inject platform-wide settings into all templates.
"""
from django.conf import settings
from django.core.exceptions import DisallowedHost


PRIVATE_PATH_PREFIXES = (
    "/accounts/",
    "/admin/",
    "/api/",
    "/dashboard/",
    "/__debug__/",
)


def platform_settings(request):
    try:
        canonical_url = request.build_absolute_uri(request.path)
    except DisallowedHost:
        canonical_base = settings.PLATFORM_URL.rstrip("/")
        canonical_url = f"{canonical_base}{request.path}"
    seo_description = (
        "Create trusted QR codes for payments, menus, products, events, and campaigns "
        "with safety checks, editable destinations, scan analytics, and exports."
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
