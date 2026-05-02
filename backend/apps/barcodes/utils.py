"""
Barcode generation engine using python-barcode + Pillow.
"""
import io
import logging
from typing import Tuple

import barcode
from barcode.writer import ImageWriter, SVGWriter

logger = logging.getLogger(__name__)

BARCODE_FORMAT_MAP = {
    "code128": "code128",
    "code39": "code39",
    "ean13": "ean13",
    "ean8": "ean8",
    "upca": "upca",
    "itf": "itf",
    "gs1_128": "gs1_128",
}

UNSUPPORTED_FORMATS = {"upce"}


def _get_writer_options(
    foreground_color: str = "#000000",
    background_color: str = "#FFFFFF",
    width: int = 300,
    height: int = 100,
    font_size: int = 10,
    show_text: bool = True,
) -> dict:
    return {
        "module_width": width / 100,
        "module_height": height / 10,
        "font_size": font_size,
        "text_distance": 5,
        "background": background_color,
        "foreground": foreground_color,
        "write_text": show_text,
        "quiet_zone": 6.5,
    }


def generate_barcode_png(
    content: str,
    barcode_format: str,
    foreground_color: str = "#000000",
    background_color: str = "#FFFFFF",
    width: int = 300,
    height: int = 100,
    font_size: int = 10,
    show_text: bool = True,
) -> bytes:
    """Return PNG bytes of a barcode."""
    if barcode_format in UNSUPPORTED_FORMATS:
        raise ValueError("UPC-E is not supported by the installed barcode engine.")
    fmt = BARCODE_FORMAT_MAP.get(barcode_format, "code128")
    try:
        bc_class = barcode.get_barcode_class(fmt)
    except barcode.errors.BarcodeNotFoundError:
        bc_class = barcode.get_barcode_class("code128")

    writer = ImageWriter()
    options = _get_writer_options(foreground_color, background_color, width, height, font_size, show_text)

    try:
        bc = bc_class(content, writer=writer)
    except Exception as exc:
        # Some formats have strict validation; fall back to code128
        logger.warning("Barcode validation failed (%s), falling back to code128: %s", barcode_format, exc)
        bc = barcode.get("code128", content, writer=writer)

    buf = io.BytesIO()
    bc.write(buf, options=options)
    buf.seek(0)
    return buf.read()


def generate_barcode_svg(
    content: str,
    barcode_format: str,
    foreground_color: str = "#000000",
    background_color: str = "#FFFFFF",
    width: int = 300,
    height: int = 100,
    show_text: bool = True,
) -> str:
    """Return SVG string of a barcode."""
    if barcode_format in UNSUPPORTED_FORMATS:
        raise ValueError("UPC-E is not supported by the installed barcode engine.")
    fmt = BARCODE_FORMAT_MAP.get(barcode_format, "code128")
    try:
        bc_class = barcode.get_barcode_class(fmt)
    except barcode.errors.BarcodeNotFoundError:
        bc_class = barcode.get_barcode_class("code128")

    writer = SVGWriter()
    options = {
        "module_width": width / 100,
        "module_height": height / 10,
        "font_size": 10 if show_text else 0,
        "background": background_color,
        "foreground": foreground_color,
        "write_text": show_text,
    }

    try:
        bc = bc_class(content, writer=writer)
    except Exception:
        bc = barcode.get("code128", content, writer=writer)

    buf = io.BytesIO()
    bc.write(buf, options=options)
    buf.seek(0)
    return buf.read().decode("utf-8")


def validate_barcode_content(content: str, barcode_format: str) -> Tuple[bool, str]:
    """Validate content for a given barcode format. Returns (is_valid, error_msg)."""
    fmt = barcode_format.lower()

    if not content:
        return False, "Content cannot be empty."
    if len(content) > 1000:
        return False, "Content is too long."
    if fmt in UNSUPPORTED_FORMATS:
        return False, "UPC-E is not supported yet. Use UPC-A or Code 128."
    if fmt not in BARCODE_FORMAT_MAP:
        return False, "Unsupported barcode format."

    if fmt == "ean13":
        digits = content.replace("-", "").replace(" ", "")
        if not digits.isdigit():
            return False, "EAN-13 must contain only digits."
        if len(digits) not in (12, 13):
            return False, "EAN-13 must be 12 or 13 digits."

    elif fmt == "ean8":
        digits = content.replace("-", "").replace(" ", "")
        if not digits.isdigit():
            return False, "EAN-8 must contain only digits."
        if len(digits) not in (7, 8):
            return False, "EAN-8 must be 7 or 8 digits."

    elif fmt in ("upca", "upce"):
        digits = content.replace("-", "").replace(" ", "")
        if not digits.isdigit():
            return False, "UPC must contain only digits."
        if fmt == "upca" and len(digits) not in (11, 12):
            return False, "UPC-A must be 11 or 12 digits."

    elif fmt == "code39":
        valid = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ -.$/+%")
        if not all(c in valid for c in content.upper()):
            return False, "Code 39 only supports A-Z, 0-9, and - . $ / + % space."

    return True, ""
