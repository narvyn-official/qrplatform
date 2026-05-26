"""Business domain verification checks.

All checks are performed server-side. Domain input is normalized and public
network checks are done before any HTTP fetch so verification cannot be used as
an SSRF probe.
"""
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import requests
from django.core.exceptions import ValidationError

from apps.accounts.models import BusinessVerification


VERIFY_USER_AGENT = "Narvyn Business Verification/1.0"


def normalize_business_domain(value):
    raw = (value or "").strip().lower()
    if not raw:
        raise ValidationError("Domain is required.")
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    domain = (parsed.hostname or "").lower().rstrip(".")
    if not domain:
        raise ValidationError("Enter a valid domain.")
    if len(domain) > 255:
        raise ValidationError("Domain is too long.")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValidationError("Enter a valid domain.") from exc
    if domain in {"localhost", "0.0.0.0"} or domain.endswith(".localhost"):
        raise ValidationError("Internal domains cannot be verified.")
    if _is_ip_literal(domain):
        raise ValidationError("Use an official business domain, not an IP address.")
    if "." not in domain:
        raise ValidationError("Enter a public domain such as example.com.")
    if not re.fullmatch(r"[a-z0-9.-]+", domain):
        raise ValidationError("Domain contains unsupported characters.")
    return domain


def verify_business_domain(verification):
    if verification.method == BusinessVerification.Method.DNS:
        return _verify_dns_txt(verification)
    if verification.method == BusinessVerification.Method.HTML_FILE:
        return _verify_html_file(verification)
    if verification.method == BusinessVerification.Method.META_TAG:
        return _verify_meta_tag(verification)
    return False, "Unsupported verification method."


def verification_instructions(verification):
    if not verification:
        return {}
    if verification.method == BusinessVerification.Method.DNS:
        return {
            "title": "Add a DNS TXT record",
            "steps": [
                f"Open DNS settings for {verification.domain}.",
                f"Create a TXT record named {verification.dns_record_name}.",
                f"Set the TXT value to {verification.dns_record_value}.",
                "Save the record, wait for DNS propagation, then click Verify.",
            ],
            "copy_label": "TXT value",
            "copy_value": verification.dns_record_value,
        }
    if verification.method == BusinessVerification.Method.HTML_FILE:
        return {
            "title": "Upload an HTML verification file",
            "steps": [
                "Create a file named narvyn-verification.html.",
                f"Put only this token inside the file: {verification.verification_token}.",
                f"Upload it so it opens at {verification.html_file_url}.",
                "Click Verify after the file is publicly reachable.",
            ],
            "copy_label": "File content",
            "copy_value": verification.verification_token,
        }
    return {
        "title": "Add a homepage meta tag",
        "steps": [
            f"Open the HTML head for https://{verification.domain}/.",
            "Add the Narvyn verification meta tag before the closing head tag.",
            "Publish the page, then click Verify.",
        ],
        "copy_label": "Meta tag",
        "copy_value": verification.meta_tag_value,
    }


def _verify_dns_txt(verification):
    expected = verification.dns_record_value
    try:
        response = requests.get(
            "https://dns.google/resolve",
            params={"name": verification.dns_record_name, "type": "TXT"},
            timeout=(2, 5),
            headers={"User-Agent": VERIFY_USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        return False, "Could not query DNS TXT records right now."
    except ValueError:
        return False, "DNS resolver returned an unreadable response."

    records = [
        str(answer.get("data", "")).replace('" "', "").strip('"')
        for answer in payload.get("Answer", [])
        if answer.get("type") == 16
    ]
    if expected in records:
        return True, "DNS TXT record matched."
    return False, "DNS TXT record was not found yet."


def _verify_html_file(verification):
    url = verification.html_file_url
    ok, body_or_message = _safe_fetch_public_url(url, allowed_domain=verification.domain)
    if not ok:
        return False, body_or_message
    if verification.verification_token in body_or_message:
        return True, "HTML verification file matched."
    return False, "Verification token was not found in the HTML file."


def _verify_meta_tag(verification):
    url = f"https://{verification.domain}/"
    ok, body_or_message = _safe_fetch_public_url(url, allowed_domain=verification.domain)
    if not ok:
        return False, body_or_message
    escaped = re.escape(verification.verification_token)
    pattern = rf'<meta\s+[^>]*(name=["\']narvyn-verification["\'])[^>]*(content=["\']{escaped}["\'])[^>]*>'
    reverse_pattern = rf'<meta\s+[^>]*(content=["\']{escaped}["\'])[^>]*(name=["\']narvyn-verification["\'])[^>]*>'
    if re.search(pattern, body_or_message, re.IGNORECASE) or re.search(reverse_pattern, body_or_message, re.IGNORECASE):
        return True, "Homepage meta tag matched."
    return False, "Narvyn verification meta tag was not found."


def _safe_fetch_public_url(url, *, allowed_domain):
    current_url = url
    for hop in range(4):
        parsed = urlparse(current_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https":
            return False, "Verification URL must use HTTPS."
        if not _domain_matches(host, allowed_domain):
            return False, "Verification redirected to a different domain."
        if _host_is_private(host):
            return False, "Verification domain resolves to a private or internal network."
        response = None
        try:
            response = requests.get(
                current_url,
                allow_redirects=False,
                timeout=(2, 5),
                headers={"User-Agent": VERIFY_USER_AGENT},
                stream=True,
            )
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location", "")
                if not location:
                    return False, "Verification redirected without a destination."
                if hop >= 3:
                    return False, "Verification URL redirected too many times."
                current_url = urljoin(current_url, location)
                continue
            if response.status_code >= 400:
                return False, f"Verification URL returned HTTP {response.status_code}."
            body = b""
            for chunk in response.iter_content(chunk_size=8192):
                body += chunk
                if len(body) >= 131072:
                    break
            return True, body.decode(response.encoding or "utf-8", errors="ignore")
        except requests.RequestException:
            return False, "Could not fetch the verification URL right now."
        finally:
            if response is not None:
                response.close()
    return False, "Could not verify the domain right now."


def _domain_matches(host, domain):
    host = (host or "").lower().rstrip(".")
    domain = (domain or "").lower().rstrip(".")
    return host == domain or host.endswith(f".{domain}")


def _is_ip_literal(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _host_is_private(hostname):
    if not hostname:
        return True
    try:
        addresses = socket.getaddrinfo(hostname, None)
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
