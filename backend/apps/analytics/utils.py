"""
Analytics utilities — IP geolocation, user-agent parsing, and event processing.
"""
import hashlib
import logging
from typing import Optional, Dict, Any
from django.utils import timezone

import user_agents
import requests

logger = logging.getLogger(__name__)

# Lightweight IP-API (free tier — replace with MaxMind GeoIP2 in production)
IP_API_URL = "http://ip-api.com/json/{ip}?fields=status,country,countryCode,regionName,city,lat,lon"
IP_API_TIMEOUT = 3  # seconds


def parse_user_agent(ua_string: str) -> Dict[str, str]:
    """Parse raw User-Agent string into structured device info."""
    if not ua_string:
        return {"device_type": "unknown", "os_family": "unknown", "browser_family": "unknown"}

    try:
        ua = user_agents.parse(ua_string)

        if ua.is_bot:
            device_type = "bot"
        elif ua.is_mobile:
            device_type = "mobile"
        elif ua.is_tablet:
            device_type = "tablet"
        elif ua.is_pc:
            device_type = "desktop"
        else:
            device_type = "unknown"

        return {
            "device_type": device_type,
            "os_family": ua.os.family[:50] if ua.os.family else "unknown",
            "browser_family": ua.browser.family[:50] if ua.browser.family else "unknown",
        }
    except Exception as exc:
        logger.warning("UA parse error: %s", exc)
        return {"device_type": "unknown", "os_family": "unknown", "browser_family": "unknown"}


def geolocate_ip(ip_address: str) -> Dict[str, Any]:
    """
    Lookup geographic info for an IP address.
    Uses ip-api.com in development; replace with MaxMind GeoIP2 in production
    for higher accuracy and no request limits.
    """
    if not ip_address or ip_address in ("127.0.0.1", "::1", "localhost"):
        return _unknown_geo()

    try:
        resp = requests.get(IP_API_URL.format(ip=ip_address), timeout=IP_API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "success":
            return _unknown_geo()

        return {
            "country_code": data.get("countryCode", "")[:2],
            "country_name": data.get("country", ""),
            "region": data.get("regionName", ""),
            "city": data.get("city", ""),
            "latitude": data.get("lat"),
            "longitude": data.get("lon"),
        }
    except Exception as exc:
        logger.warning("Geolocation failed for %s: %s", ip_address, exc)
        return _unknown_geo()


def geolocate_ip_maxmind(ip_address: str) -> Dict[str, Any]:
    """
    MaxMind GeoIP2 lookup — preferred for production (no rate limits, offline).
    Requires GeoLite2-City.mmdb in settings.GEOIP_PATH.
    """
    try:
        import geoip2.database
        from django.conf import settings

        with geoip2.database.Reader(f"{settings.GEOIP_PATH}/{settings.GEOIP_CITY}") as reader:
            record = reader.city(ip_address)
            return {
                "country_code": record.country.iso_code or "",
                "country_name": record.country.name or "",
                "region": record.subdivisions.most_specific.name or "",
                "city": record.city.name or "",
                "latitude": record.location.latitude,
                "longitude": record.location.longitude,
            }
    except Exception as exc:
        logger.warning("MaxMind lookup failed for %s: %s", ip_address, exc)
        return _unknown_geo()


def _unknown_geo() -> Dict[str, Any]:
    return {
        "country_code": "",
        "country_name": "",
        "region": "",
        "city": "",
        "latitude": None,
        "longitude": None,
    }


def compute_scan_fingerprint(ip: str, ua: str, date_str: str) -> str:
    raw = f"{ip}:{ua}:{date_str}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get_client_ip(request) -> Optional[str]:
    """Extract real client IP from request, respecting proxy headers."""
    from ipware import get_client_ip as _get_ip
    ip, _ = _get_ip(request)
    return ip or request.META.get("REMOTE_ADDR")
