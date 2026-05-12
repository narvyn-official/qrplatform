import json

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.db import connection
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from apps.accounts.payments import paytm_configured
from apps.accounts.plans import PLAN_CATALOG


HOME_FEATURES = [
    {
        "icon": "scan",
        "bg": "bg-primary-50 text-primary-600",
        "title": "Dynamic destinations",
        "desc": "Update campaign links after printing without replacing the QR code.",
    },
    {
        "icon": "chart",
        "bg": "bg-emerald-50 text-emerald-600",
        "title": "Scan analytics",
        "desc": "Track total scans, unique visitors, devices, browsers, and locations.",
    },
    {
        "icon": "shield",
        "bg": "bg-amber-50 text-amber-600",
        "title": "Access controls",
        "desc": "Use scan limits, expiry dates, and passwords for protected QR journeys.",
    },
]


def home(request):
    site_url = settings.PLATFORM_URL.rstrip("/")
    schema = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "@id": f"{site_url}/#organization",
                "name": settings.PLATFORM_NAME,
                "url": site_url,
                "logo": f"{site_url}/static/pwa/icon-512.png",
            },
            {
                "@type": "WebSite",
                "@id": f"{site_url}/#website",
                "name": settings.PLATFORM_NAME,
                "url": site_url,
                "publisher": {"@id": f"{site_url}/#organization"},
            },
            {
                "@type": "SoftwareApplication",
                "name": settings.PLATFORM_NAME,
                "applicationCategory": "BusinessApplication",
                "operatingSystem": "Web",
                "url": site_url,
                "description": (
                    "Create dynamic QR codes, editable campaign links, barcodes, exports, "
                    "and scan analytics from one web dashboard."
                ),
                "offers": {"@type": "Offer", "price": "0", "priceCurrency": "INR"},
            },
        ],
    }
    return render(request, "core/home.html", {
        "features": HOME_FEATURES,
        "schema_json": json.dumps(schema),
    })


def health_check(request):
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False
    status = 200 if db_ok else 503
    return JsonResponse({"status": "ok" if db_ok else "degraded", "db": db_ok}, status=status)


def pricing(request):
    site_url = settings.PLATFORM_URL.rstrip("/")
    plans = [PLAN_CATALOG["free"], PLAN_CATALOG["pro"], PLAN_CATALOG["enterprise"]]
    schema = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": f"{settings.PLATFORM_NAME} membership plans",
        "description": "Membership plans for dynamic QR codes, scan analytics, exports, and API access.",
        "brand": {"@type": "Brand", "name": settings.PLATFORM_NAME},
        "offers": [
            {
                "@type": "Offer",
                "name": plan.name,
                "url": f"{site_url}{reverse('core:pricing')}",
                "price": str(plan.monthly_price_inr),
                "priceCurrency": "INR",
                "availability": "https://schema.org/InStock",
            }
            for plan in plans
        ],
    }
    return render(request, "core/pricing.html", {
        "plans": plans,
        "gateway_configured": paytm_configured(),
        "schema_json": json.dumps(schema),
    })


def terms(request):
    return render(request, "core/terms.html")


def offline(request):
    return render(request, "core/offline.html")


def manifest(request):
    start_url = reverse("core:home")
    return JsonResponse({
        "id": "/",
        "name": settings.PLATFORM_NAME,
        "short_name": settings.PLATFORM_NAME,
        "description": "Create dynamic QR codes, barcodes, and scan analytics.",
        "start_url": start_url,
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone", "minimal-ui"],
        "background_color": "#f8fafc",
        "theme_color": "#4f46e5",
        "orientation": "portrait-primary",
        "categories": ["productivity", "business", "utilities"],
        "icons": [
            {
                "src": "/static/pwa/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "/static/pwa/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "/static/pwa/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }
        ],
        "shortcuts": [
            {"name": "New QR Code", "url": "/dashboard/qrcodes/create/", "description": "Create a QR code"},
            {"name": "New Barcode", "url": "/barcodes/create/", "description": "Create a barcode"},
            {"name": "Scan QR", "url": "/dashboard/scan/", "description": "Open the QR scanner"},
        ],
    }, content_type="application/manifest+json")


def service_worker(request):
    script = """
const CACHE_NAME = 'qrflow-shell-v2';
const OFFLINE_URL = '/offline/';
const CORE_ASSETS = [
  OFFLINE_URL,
  '/static/pwa/icon-192.png',
  '/static/pwa/icon-512.png',
  '/static/pwa/icon.svg'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(CORE_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => cached || fetch(request).then(response => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
        return response;
      }))
    );
  }
});
""".strip()
    return HttpResponse(script, content_type="application/javascript")


def _site_url(path):
    return f"{settings.PLATFORM_URL.rstrip('/')}{path}"


def robots_txt(request):
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /accounts/",
        "Disallow: /api/",
        "Disallow: /dashboard/",
        "Disallow: /__debug__/",
        f"Sitemap: {_site_url(reverse('core:sitemap_xml'))}",
        "",
    ])
    return HttpResponse(body, content_type="text/plain")


def sitemap_xml(request):
    today = timezone.now().date().isoformat()
    urls = [
        {"loc": _site_url(reverse("core:home")), "priority": "1.0", "changefreq": "weekly"},
        {"loc": _site_url(reverse("core:pricing")), "priority": "0.8", "changefreq": "weekly"},
        {"loc": _site_url(reverse("core:terms")), "priority": "0.3", "changefreq": "yearly"},
    ]
    items = "\n".join(
        "  <url>\n"
        f"    <loc>{item['loc']}</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        f"    <changefreq>{item['changefreq']}</changefreq>\n"
        f"    <priority>{item['priority']}</priority>\n"
        "  </url>"
        for item in urls
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{items}\n"
        "</urlset>\n"
    )
    return HttpResponse(body, content_type="application/xml")


def bad_request(request, exception=None):
    return render(request, "errors/400.html", status=400)


def permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)


def page_not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


def server_error(request):
    return render(request, "errors/500.html", status=500)
