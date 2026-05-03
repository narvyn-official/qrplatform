"""
Premium QR utilities that stay local and testable.

External production integrations can replace these helpers without changing the
dashboard views: a malware intelligence API, DNS custom-domain verifier, or
Zapier connector would live behind the same result dictionaries.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from django.db.models import F
from django.utils import timezone

from apps.analytics.utils import parse_user_agent
from apps.qrcodes.models import QRDestinationRule


SUSPICIOUS_KEYWORDS = (
    "phishing",
    "malware",
    "credential",
    "wallet-verify",
    "login-verify",
    "free-gift",
    "account-alert",
)


def assess_destination(url: str) -> dict:
    """Return destination health and heuristic trust result."""
    parsed = urlparse(url or "")
    issues = []
    if parsed.scheme not in ("http", "https"):
        return {
            "status": "broken",
            "status_code": None,
            "final_url": url or "",
            "response_ms": 0,
            "is_safe": False,
            "issue": "Destination must use http or https.",
        }

    host = (parsed.hostname or "").lower()
    if any(word in host or word in parsed.path.lower() for word in SUSPICIOUS_KEYWORDS):
        issues.append("Suspicious phishing or malware keyword detected.")
    if parsed.scheme != "https":
        issues.append("Destination is not HTTPS.")
    if host.startswith("xn--"):
        issues.append("Internationalized domain detected. Verify brand spelling.")

    started = time.monotonic()
    status_code = None
    final_url = url
    network_issue = ""
    try:
        response = requests.head(url, allow_redirects=True, timeout=4)
        if response.status_code in (405, 403):
            response = requests.get(url, allow_redirects=True, timeout=4, stream=True)
        status_code = response.status_code
        final_url = response.url or url
        response.close()
    except requests.RequestException as exc:
        network_issue = str(exc)

    response_ms = int((time.monotonic() - started) * 1000)
    broken = bool(network_issue) or (status_code is not None and status_code >= 400)
    if broken:
        issues.append(network_issue or f"Destination returned HTTP {status_code}.")

    return {
        "status": "broken" if broken else ("warning" if issues else "ok"),
        "status_code": status_code,
        "final_url": final_url,
        "response_ms": response_ms,
        "is_safe": not any("Suspicious" in issue for issue in issues),
        "issue": " ".join(issues),
    }


def build_preprint_check(qr, health=None) -> dict:
    """Score print readiness: size, contrast, logo, destination and tracking."""
    checks = []

    def add(label, ok, detail, severity="info"):
        checks.append({"label": label, "ok": ok, "detail": detail, "severity": severity})

    add(
        "Print size",
        qr.qr_size >= 300,
        "Use at least 300px for small labels; 600px+ for posters.",
        "warning",
    )
    add(
        "Contrast",
        qr.foreground_color.lower() != qr.background_color.lower(),
        "Foreground and background colors should be visibly different.",
        "danger",
    )
    add(
        "Logo clearance",
        not qr.logo or qr.error_correction == "H",
        "Use high error correction when a logo sits inside the QR.",
        "warning",
    )
    add(
        "Tracking URL",
        qr.qr_type in (qr.QRType.URL, qr.QRType.DYNAMIC),
        "URL QR codes encode the platform redirect URL so scans are counted.",
        "info",
    )
    add(
        "Schedule",
        not qr.is_scheduled_inactive and not qr.is_expired,
        "QR is active for the current date and scan limit.",
        "danger",
    )

    if health:
        add(
            "Destination health",
            health.status == "ok",
            health.issue or f"Last check: {health.get_status_display()}",
            "danger" if health.status == "broken" else "warning",
        )

    passed = sum(1 for check in checks if check["ok"])
    score = round((passed / len(checks)) * 100) if checks else 100
    return {"score": score, "checks": checks}


@dataclass
class DestinationResolution:
    url: str
    rule: QRDestinationRule | None = None


def resolve_smart_destination(qr, request, default_destination: str) -> DestinationResolution:
    """Resolve device/language/country/time/A-B smart destination rules."""
    rules = list(qr.destination_rules.filter(is_active=True))
    if not rules:
        return DestinationResolution(default_destination)

    ua_info = parse_user_agent(request.META.get("HTTP_USER_AGENT", ""))
    language = (request.META.get("HTTP_ACCEPT_LANGUAGE", "")[:2] or "").lower()
    country = (request.META.get("HTTP_CF_IPCOUNTRY", "") or request.META.get("HTTP_X_COUNTRY_CODE", "")).upper()
    now_local = timezone.localtime()

    for rule in rules:
        match = (rule.match_value or "").strip().lower()
        if rule.rule_type == QRDestinationRule.RuleType.DEVICE and match == ua_info["device_type"]:
            return _hit(rule)
        if rule.rule_type == QRDestinationRule.RuleType.LANGUAGE and match == language:
            return _hit(rule)
        if rule.rule_type == QRDestinationRule.RuleType.COUNTRY and match.upper() == country:
            return _hit(rule)
        if rule.rule_type == QRDestinationRule.RuleType.TIME and _time_match(match, now_local):
            return _hit(rule)

    ab_rules = [rule for rule in rules if rule.rule_type == QRDestinationRule.RuleType.AB]
    if ab_rules:
        total = sum(max(rule.weight, 1) for rule in ab_rules)
        pick = random.randint(1, total)
        cursor = 0
        for rule in ab_rules:
            cursor += max(rule.weight, 1)
            if pick <= cursor:
                return _hit(rule)

    return DestinationResolution(default_destination)


def _hit(rule):
    QRDestinationRule.objects.filter(pk=rule.pk).update(hits=F("hits") + 1)
    rule.hits += 1
    return DestinationResolution(rule.destination_url, rule)


def _time_match(match_value: str, now_local) -> bool:
    try:
        start, end = match_value.replace(" ", "").split("-", 1)
        start_hour, start_minute = [int(part) for part in start.split(":", 1)]
        end_hour, end_minute = [int(part) for part in end.split(":", 1)]
        current = now_local.hour * 60 + now_local.minute
        start_value = start_hour * 60 + start_minute
        end_value = end_hour * 60 + end_minute
        if start_value <= end_value:
            return start_value <= current <= end_value
        return current >= start_value or current <= end_value
    except Exception:
        return False
