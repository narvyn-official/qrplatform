"""Shared QR validation helpers.

Keep field-level normalization here so HTML forms and DRF serializers do not
quietly drift apart over time.
"""
import re

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator


HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s().-]{6,20}$")
HTTP_URL_VALIDATOR = URLValidator(schemes=("http", "https"))
EMAIL_VALIDATOR = EmailValidator()
MIN_QR_SIZE = 128
MAX_QR_SIZE = 2048
MAX_LOGO_SIZE_RATIO = 0.35


def normalize_http_url(value):
    url = (value or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    if url:
        HTTP_URL_VALIDATOR(url)
    return url


def validate_hex_color(value, field_label):
    color = (value or "").strip()
    if not HEX_COLOR_RE.match(color):
        raise ValidationError({field_label: "Use a valid hex color, for example #000000."})
    return color.upper()


def validate_phone_number(value, field_label, message):
    phone = (value or "").strip()
    if not PHONE_RE.match(phone):
        raise ValidationError({field_label: message})
    return phone
