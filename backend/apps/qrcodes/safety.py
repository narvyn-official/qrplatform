"""Safety analysis for scanned QR payloads.

The scanner must preview links without automatically opening them. Network
checks here are intentionally conservative and avoid private/internal hosts so
the redirect-chain feature cannot become an SSRF primitive.
"""
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests
from django.core.exceptions import ValidationError

from apps.qrcodes.validation import normalize_http_url


SHORTENER_HOSTS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd",
    "buff.ly", "rebrand.ly", "cutt.ly", "shorturl.at", "lnkd.in",
}
RISKY_EXTENSIONS = (
    ".apk", ".exe", ".dmg", ".pkg", ".msi", ".scr", ".bat", ".cmd",
    ".js", ".vbs", ".jar", ".zip", ".rar", ".7z",
)
URL_ANALYZE_RISKY_EXTENSIONS = (".apk", ".exe", ".bat", ".scr", ".zip")
SUSPICIOUS_KEYWORDS = (
    "login", "verify", "reward", "free", "gift", "urgent", "kyc",
    "update-password", "account-locked",
)
SUSPICIOUS_TLDS = {
    "zip", "mov", "click", "top", "xyz", "gq", "tk", "ml", "cf", "work",
    "quest", "rest", "support", "country",
}
LOOKALIKE_BRANDS = {
    "google", "paytm", "phonepe", "amazon", "apple", "microsoft", "facebook",
    "instagram", "whatsapp", "paypal", "narvyn",
}
OPENABLE_ACTION_SCHEMES = ("mailto:", "tel:", "sms:", "smsto:")


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_title = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and len(" ".join(self.parts)) < 180:
            self.parts.append(data.strip())

    @property
    def title(self):
        return re.sub(r"\s+", " ", " ".join(part for part in self.parts if part)).strip()[:180]


def _host_is_private(hostname):
    if not hostname:
        return True
    host = hostname.lower().rstrip(".")
    if host in {"localhost", "0.0.0.0"} or host.endswith(".localhost"):
        return True
    try:
        addresses = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


def _base_analysis(value):
    return {
        "input": (value or "").strip()[:4000],
        "kind": "text",
        "normalized_url": "",
        "display_url": "",
        "scheme": "",
        "host": "",
        "risk_level": "safe",
        "summary": "Plain text QR. Review it before copying or using it.",
        "warnings": [],
        "redirect_chain": [],
        "can_open": False,
    }


def analyze_scanned_value(value, *, follow_redirects=True):
    """Return a JSON-serializable risk preview for a scanned QR payload."""
    analysis = _base_analysis(value)
    raw = analysis["input"]
    if not raw:
        analysis.update(risk_level="warning", summary="No QR value was provided.")
        return analysis

    lower = raw.lower()
    if lower.startswith(("mailto:", "tel:", "sms:", "smsto:")):
        analysis.update(
            kind="action",
            scheme=raw.split(":", 1)[0].lower(),
            display_url=raw,
            can_open=True,
            summary="This QR opens another app. Confirm the content before continuing.",
        )
        return analysis

    if not (lower.startswith(("http://", "https://")) or lower.startswith("www.")):
        return analysis

    try:
        normalized_url = normalize_http_url(raw)
    except ValidationError:
        analysis.update(
            kind="url",
            risk_level="danger",
            summary="This does not look like a valid web address.",
            warnings=["Invalid URL format."],
        )
        return analysis

    parsed = urlparse(normalized_url)
    host = (parsed.hostname or "").lower()
    analysis.update(
        kind="url",
        normalized_url=normalized_url,
        display_url=normalized_url,
        scheme=parsed.scheme,
        host=host,
        can_open=True,
        summary="URL preview ready. Check the domain before opening.",
    )

    warnings = analysis["warnings"]
    if parsed.scheme != "https":
        warnings.append("This link does not use HTTPS.")
    if "@" in parsed.netloc:
        warnings.append("The URL contains an @ symbol, which can hide the real destination.")
    if host.startswith("xn--") or ".xn--" in host:
        warnings.append("The domain uses internationalized characters. Verify it carefully.")
    if host in SHORTENER_HOSTS:
        warnings.append("This is a shortened URL, so the final destination may be hidden.")
    if _is_ip_literal(host):
        warnings.append("The destination uses a raw IP address instead of a business domain.")
    if parsed.path.lower().endswith(RISKY_EXTENSIONS):
        warnings.append("This link points to a downloadable file type commonly abused for malware.")

    if _host_is_private(host):
        analysis.update(
            risk_level="danger",
            can_open=False,
            summary="Blocked because the destination points to a private or internal network address.",
        )
        warnings.append("Private/internal network destination blocked.")
        return analysis

    if follow_redirects:
        _append_redirect_chain(analysis, normalized_url)

    if "Blocked" in analysis["summary"]:
        analysis["risk_level"] = "danger"
    elif any(
        phrase in " ".join(warnings).lower()
        for phrase in ("downloadable", "hidden", "not use https", "raw ip", "@ symbol")
    ):
        analysis["risk_level"] = "warning"
        analysis["summary"] = "Review warnings before opening this link."
    return analysis


def analyze_url(input_url, *, fetch_title=False):
    """Safely analyze a URL without following redirects to private networks."""
    raw = (input_url or "").strip()[:4000]
    result = {
        "inputUrl": raw,
        "normalizedUrl": "",
        "finalUrl": "",
        "domain": "",
        "finalDomain": "",
        "redirectChain": [],
        "redirectCount": 0,
        "isHttps": False,
        "usesUrlShortener": False,
        "hasSuspiciousKeywords": False,
        "hasExecutableDownload": False,
        "riskLevel": "safe",
        "riskScore": 0,
        "warnings": [],
    }
    if fetch_title:
        result["pageTitle"] = ""

    if not raw:
        result["warnings"].append("URL is required.")
        _finalize_url_result(result, 40)
        return result

    try:
        normalized_url = normalize_http_url(raw)
    except ValidationError:
        result["warnings"].append("Invalid URL format.")
        _finalize_url_result(result, 80)
        return result

    parsed = urlparse(normalized_url)
    domain = (parsed.hostname or "").lower()
    result.update(
        normalizedUrl=normalized_url,
        finalUrl=normalized_url,
        domain=domain,
        finalDomain=domain,
        isHttps=parsed.scheme == "https",
        usesUrlShortener=domain in SHORTENER_HOSTS,
        hasSuspiciousKeywords=_has_suspicious_keywords(normalized_url),
        hasExecutableDownload=_has_executable_download(parsed.path),
    )

    risk = _score_url_static_signals(result, parsed, domain)

    if _host_is_private(domain):
        result["warnings"].append("Blocked localhost, private IP, or internal network destination.")
        result["warnings"].append("Private/internal network destination blocked.")
        _finalize_url_result(result, risk + 100)
        return result

    redirect_preview = _safe_redirect_chain(normalized_url, fetch_title=fetch_title)
    result["redirectChain"] = redirect_preview["redirectChain"]
    result["redirectCount"] = redirect_preview["redirectCount"]
    if redirect_preview["finalUrl"]:
        result["finalUrl"] = redirect_preview["finalUrl"]
        final = urlparse(result["finalUrl"])
        result["finalDomain"] = (final.hostname or "").lower()
    if fetch_title:
        result["pageTitle"] = redirect_preview.get("pageTitle", "")
    if redirect_preview["warning"]:
        risk += 20
        result["warnings"].append(redirect_preview["warning"])

    if result["redirectCount"] > 0:
        risk += 10
        result["warnings"].append("This link redirects before reaching the final page.")
    if result["redirectCount"] >= 3:
        risk += 20
        result["warnings"].append("This link uses multiple redirects.")
    if result["finalDomain"] and result["finalDomain"] != result["domain"]:
        risk += 25
        result["warnings"].append("The original domain and final domain do not match.")
    final_parsed = urlparse(result["finalUrl"])
    if final_parsed.scheme and final_parsed.scheme != "https":
        risk += 10 if parsed.scheme != "https" else 30

    _finalize_url_result(result, risk)
    return result


def analyze_scanner_content(content, *, fetch_title=True):
    """Analyze scanned QR content for the public Safe QR Scanner API."""
    raw = (content or "").strip()[:4000]
    result = {
        "type": _detect_content_type(raw),
        "rawContent": raw,
        "inputUrl": "",
        "normalizedUrl": "",
        "domain": "",
        "finalDomain": "",
        "finalUrl": "",
        "pageTitle": "",
        "isHttps": False,
        "redirectCount": 0,
        "redirectChain": [],
        "usesUrlShortener": False,
        "hasSuspiciousKeywords": False,
        "hasExecutableDownload": False,
        "riskScore": 0,
        "riskLevel": "safe",
        "warnings": [],
        "recommendation": "This QR content does not open a web page. Review it before using it.",
    }
    if not raw:
        result.update(
            type="unknown",
            riskScore=35,
            riskLevel="caution",
            warnings=["No QR content was provided."],
            recommendation="Scan again or upload a clearer QR image.",
        )
        return _apply_safety_score(result)

    risk = 0
    warnings = result["warnings"]

    if result["type"] == "upi":
        risk += _score_upi(raw, warnings)
        _finalize_scanner_result(result, risk)
        return result

    if result["type"] != "url":
        _finalize_scanner_result(result, risk)
        return result

    url_analysis = analyze_url(raw, fetch_title=fetch_title)
    if not url_analysis["normalizedUrl"]:
        result.update(
            riskScore=80,
            riskLevel="risky",
            warnings=url_analysis["warnings"] or ["Invalid URL format."],
            recommendation="Do not open this link. Ask the sender for a valid business URL.",
        )
        return _apply_safety_score(result)

    result.update(
        inputUrl=url_analysis["inputUrl"],
        normalizedUrl=url_analysis["normalizedUrl"],
        domain=url_analysis["finalDomain"] or url_analysis["domain"],
        finalDomain=url_analysis["finalDomain"],
        finalUrl=url_analysis["finalUrl"],
        pageTitle=url_analysis.get("pageTitle", ""),
        isHttps=url_analysis["isHttps"],
        redirectCount=url_analysis["redirectCount"],
        redirectChain=url_analysis["redirectChain"],
        usesUrlShortener=url_analysis["usesUrlShortener"],
        hasSuspiciousKeywords=url_analysis["hasSuspiciousKeywords"],
        hasExecutableDownload=url_analysis["hasExecutableDownload"],
        riskScore=url_analysis["riskScore"],
        safetyScore=url_analysis.get("safetyScore", max(0, 100 - int(url_analysis["riskScore"]))),
        riskLevel=url_analysis["riskLevel"],
        warnings=url_analysis["warnings"],
    )
    result["recommendation"] = (
        "Do not open this QR unless you fully trust the source."
        if result["riskLevel"] == "risky"
        else "Review the warnings and confirm the sender before opening."
        if result["riskLevel"] == "caution"
        else "Looks acceptable. Confirm the domain before opening."
    )
    return _apply_safety_score(result)


def _is_ip_literal(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _append_redirect_chain(analysis, url):
    try:
        session = requests.Session()
        session.max_redirects = 5
        response = session.get(
            url,
            allow_redirects=True,
            timeout=(2, 3),
            headers={"User-Agent": "Narvyn QR Safety Preview/1.0"},
            stream=True,
        )
        response.close()
    except requests.TooManyRedirects:
        analysis["risk_level"] = "warning"
        analysis["warnings"].append("This URL redirects too many times.")
        return
    except requests.RequestException:
        analysis["warnings"].append("Could not verify the redirect chain right now.")
        return

    chain = [item.url for item in response.history] + [response.url]
    safe_chain = []
    for item_url in chain[:6]:
        parsed = urlparse(item_url)
        host = parsed.hostname or ""
        safe_chain.append({"url": item_url[:500], "host": host[:255]})
        if _host_is_private(host):
            analysis["risk_level"] = "danger"
            analysis["can_open"] = False
            analysis["summary"] = "Blocked because a redirect points to a private or internal address."
            analysis["warnings"].append("Redirect chain contains a private/internal address.")
            break
    analysis["redirect_chain"] = safe_chain
    if len({item["host"] for item in safe_chain if item["host"]}) > 1:
        analysis["warnings"].append("This link redirects to a different domain.")


def _detect_content_type(raw):
    lower = raw.lower()
    if not raw:
        return "unknown"
    if lower.startswith(("http://", "https://", "www.")):
        return "url"
    if lower.startswith("upi://pay"):
        return "upi"
    if lower.startswith("wifi:"):
        return "wifi"
    if lower.startswith("begin:vcard"):
        return "vcard"
    if lower.startswith("mailto:") or re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", raw):
        return "email"
    if lower.startswith("tel:"):
        return "phone"
    if lower.startswith(("sms:", "smsto:")):
        return "sms"
    if lower.startswith("geo:"):
        return "location"
    if "://" in lower or ":" in lower[:18]:
        return "unknown"
    return "text"


def _score_upi(raw, warnings):
    risk = 0
    query = ""
    if "?" in raw:
        query = raw.split("?", 1)[1]
    params = dict(
        item.split("=", 1) if "=" in item else (item, "")
        for item in query.split("&")
        if item
    )
    if not params.get("pn", "").strip():
        risk += 25
        warnings.append("UPI payee name is missing.")
    if not params.get("pa", "").strip():
        risk += 50
        warnings.append("UPI payment address is missing.")
    return risk


def _has_suspicious_keywords(url):
    haystack = (url or "").lower()
    return any(keyword in haystack for keyword in SUSPICIOUS_KEYWORDS)


def _has_executable_download(path):
    clean_path = (path or "").lower().split("?", 1)[0]
    return clean_path.endswith(URL_ANALYZE_RISKY_EXTENSIONS)


def _score_url_static_signals(result, parsed, domain):
    risk = 0
    warnings = result["warnings"]
    if parsed.scheme != "https":
        risk += 20
        warnings.append("This link does not use HTTPS.")
    if result["usesUrlShortener"]:
        risk += 25
        warnings.append("This URL uses a shortener, so the destination is hidden until checked.")
    if result["hasSuspiciousKeywords"]:
        risk += 20
        warnings.append("This link contains words commonly used in phishing or scam pages.")
    if result["hasExecutableDownload"]:
        risk += 45
        warnings.append("This link points to a downloadable file type commonly abused for malware.")
    if _is_ip_literal(domain):
        risk += 25
        warnings.append("This URL uses an IP address instead of a recognizable domain.")
    if domain.startswith("xn--") or ".xn--" in domain:
        risk += 20
        warnings.append("This domain uses punycode, which can imitate trusted brand names.")
    if _is_suspicious_tld(domain):
        risk += 15
        warnings.append("This domain uses a higher-risk top-level domain.")
    if _looks_like_brand_impersonation(domain):
        risk += 30
        warnings.append("This domain looks similar to a known brand. Verify it carefully.")
    if "@" in parsed.netloc:
        risk += 25
        warnings.append("The URL contains an @ symbol, which can hide the real destination.")
    return risk


def _finalize_url_result(result, risk):
    result["riskScore"] = max(0, min(int(risk), 100))
    _apply_safety_score(result)
    if result["riskScore"] >= 60:
        result["riskLevel"] = "risky"
    elif result["riskScore"] >= 25:
        result["riskLevel"] = "caution"
    else:
        result["riskLevel"] = "safe"


def _finalize_scanner_result(result, risk, *, blocked=False):
    result["riskScore"] = max(0, min(int(risk), 100))
    _apply_safety_score(result)
    if blocked or result["riskScore"] >= 60:
        result["riskLevel"] = "risky"
        result["recommendation"] = "Do not open this QR unless you fully trust the source."
    elif result["riskScore"] >= 25:
        result["riskLevel"] = "caution"
        result["recommendation"] = "Review the warnings and confirm the sender before opening."
    elif result["type"] == "url":
        result["riskLevel"] = "safe"
        result["recommendation"] = "Looks acceptable. Confirm the domain before opening."
    else:
        result["riskLevel"] = "safe"
        result["recommendation"] = "No obvious risk detected. Review the content before using it."


def _apply_safety_score(result):
    try:
        risk_score = int(result.get("riskScore") or 0)
    except (TypeError, ValueError):
        risk_score = 0
    result["safetyScore"] = max(0, min(100, 100 - risk_score))
    return result


def _is_suspicious_tld(host):
    parts = (host or "").split(".")
    return len(parts) > 1 and parts[-1].lower() in SUSPICIOUS_TLDS


def _looks_like_brand_impersonation(host):
    label = (host or "").split(".")[0].lower()
    if not label:
        return False
    normalized = label.translate(str.maketrans({"0": "o", "1": "l", "3": "e", "5": "s", "7": "t"}))
    for brand in LOOKALIKE_BRANDS:
        if label == brand:
            return False
        if normalized == brand:
            return True
        if brand in label and label != brand and len(label) <= len(brand) + 8:
            return True
        if _levenshtein(normalized, brand) == 1 and len(brand) >= 5:
            return True
    return False


def _levenshtein(left, right):
    if abs(len(left) - len(right)) > 1:
        return 2
    previous = list(range(len(right) + 1))
    for i, char_left in enumerate(left, start=1):
        current = [i]
        for j, char_right in enumerate(right, start=1):
            cost = 0 if char_left == char_right else 1
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def _fetch_redirect_preview(url, *, fetch_title=True):
    preview = _safe_redirect_chain(url, fetch_title=fetch_title)
    preview["redirectChain"] = [item["url"] for item in preview["redirectChain"]]
    return preview


def _safe_redirect_chain(url, *, fetch_title=False):
    preview = {
        "finalUrl": "",
        "pageTitle": "",
        "redirectChain": [],
        "redirectCount": 0,
        "warning": "",
    }
    current_url = url
    session = requests.Session()

    for hop in range(6):
        parsed = urlparse(current_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"}:
            preview["warning"] = "Could not fully analyze this QR. Open with caution."
            break
        if _host_is_private(host):
            preview["warning"] = "Blocked localhost, private IP, or internal network redirect."
            break

        response = None
        try:
            response = session.get(
                current_url,
                allow_redirects=False,
                timeout=(2, 4),
                headers={"User-Agent": "Narvyn QR Safe URL Analyzer/1.0"},
                stream=True,
            )
            preview["redirectChain"].append({
                "url": current_url[:2000],
                "statusCode": response.status_code,
                "domain": host[:255],
            })

            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location", "")
                if not location:
                    preview["warning"] = "Could not fully analyze this QR. Open with caution."
                    break
                if hop >= 5:
                    preview["warning"] = "This URL redirects too many times."
                    break
                current_url = urljoin(current_url, location)
                preview["redirectCount"] += 1
                continue

            preview["finalUrl"] = response.url[:2000]
            if fetch_title and "text/html" in response.headers.get("Content-Type", "").lower():
                body = b""
                for chunk in response.iter_content(chunk_size=8192):
                    body += chunk
                    if len(body) >= 65536:
                        break
                parser = _TitleParser()
                parser.feed(body.decode(response.encoding or "utf-8", errors="ignore"))
                preview["pageTitle"] = parser.title
            break
        except requests.RequestException:
            preview["warning"] = "Could not fully analyze this QR. Open with caution."
            break
        finally:
            if response is not None:
                response.close()

    if not preview["finalUrl"] and preview["redirectChain"]:
        preview["finalUrl"] = preview["redirectChain"][-1]["url"]
    return preview
