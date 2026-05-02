"""
QR Code generation engine — supports customization, logos, and multiple formats.
"""
import io
import os
import logging
import urllib.parse
from typing import Optional, Tuple

import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import (
    SquareModuleDrawer,
    RoundedModuleDrawer,
    CircleModuleDrawer,
    GappedSquareModuleDrawer,
)
from qrcode.image.styles.colormasks import SolidFillColorMask
from qrcode import constants as qr_constants
from PIL import Image, ImageDraw, ImageFont
import qrcode.image.svg

logger = logging.getLogger(__name__)

# Map model choices → qrcode library drawers
DOT_STYLE_MAP = {
    "square": SquareModuleDrawer(),
    "rounded": RoundedModuleDrawer(),
    "dots": CircleModuleDrawer(),
    "classy": GappedSquareModuleDrawer(),
    "classy_rounded": RoundedModuleDrawer(),
    "extra_rounded": RoundedModuleDrawer(),
}

ERROR_CORRECTION_MAP = {
    "L": qr_constants.ERROR_CORRECT_L,
    "M": qr_constants.ERROR_CORRECT_M,
    "Q": qr_constants.ERROR_CORRECT_Q,
    "H": qr_constants.ERROR_CORRECT_H,
}


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def generate_qr_image(
    content: str,
    foreground_color: str = "#000000",
    background_color: str = "#FFFFFF",
    dot_style: str = "square",
    error_correction: str = "M",
    size: int = 300,
    logo_path: Optional[str] = None,
    logo_size_ratio: float = 0.2,
    frame_text: str = "",
    frame_color: str = "#000000",
) -> Image.Image:
    """
    Generate a styled PIL QR code image.

    Returns a Pillow Image object ready for saving.
    """
    ec_level = ERROR_CORRECTION_MAP.get(error_correction, qr_constants.ERROR_CORRECT_M)
    # Force H when embedding a logo to preserve readability
    if logo_path:
        ec_level = qr_constants.ERROR_CORRECT_H

    fg_rgb = hex_to_rgb(foreground_color)
    bg_rgb = hex_to_rgb(background_color)

    drawer = DOT_STYLE_MAP.get(dot_style, SquareModuleDrawer())

    qr = qrcode.QRCode(
        version=None,  # auto-size
        error_correction=ec_level,
        box_size=10,
        border=4,
    )
    qr.add_data(content)
    qr.make(fit=True)

    color_mask = SolidFillColorMask(
        front_color=fg_rgb,
        back_color=bg_rgb,
    )

    img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=drawer,
        color_mask=color_mask,
    ).convert("RGBA")

    # Resize to desired output size
    img = img.resize((size, size), Image.LANCZOS)

    # Embed logo if provided
    if logo_path and os.path.exists(logo_path):
        img = _embed_logo(img, logo_path, logo_size_ratio)

    # Add frame / caption below QR
    if frame_text:
        img = _add_frame_text(img, frame_text, frame_color, background_color)

    return img


def _embed_logo(qr_img: Image.Image, logo_path: str, size_ratio: float) -> Image.Image:
    """Overlay a logo image in the center of the QR code."""
    try:
        logo = Image.open(logo_path).convert("RGBA")
    except Exception as exc:
        logger.warning("Failed to open logo: %s", exc)
        return qr_img

    qr_size = qr_img.size[0]
    logo_size = int(qr_size * size_ratio)
    logo = logo.resize((logo_size, logo_size), Image.LANCZOS)

    # White padding around logo for readability
    padding = 6
    padded_size = logo_size + padding * 2
    background = Image.new("RGBA", (padded_size, padded_size), (255, 255, 255, 255))
    background.paste(logo, (padding, padding), logo if logo.mode == "RGBA" else None)

    # Center position
    pos = ((qr_size - padded_size) // 2, (qr_size - padded_size) // 2)
    qr_img.paste(background, pos, background)

    return qr_img


def _add_frame_text(
    qr_img: Image.Image,
    text: str,
    text_color: str,
    bg_color: str,
) -> Image.Image:
    """Add a text banner below the QR code."""
    w, h = qr_img.size
    banner_height = 40
    new_img = Image.new("RGBA", (w, h + banner_height), hex_to_rgb(bg_color) + (255,))
    new_img.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(new_img)
    font = None
    for font_path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    ):
        try:
            font = ImageFont.truetype(font_path, 18)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_x = (w - text_w) // 2
    text_y = h + (banner_height - (bbox[3] - bbox[1])) // 2
    draw.text((text_x, text_y), text, fill=hex_to_rgb(text_color), font=font)

    return new_img


def generate_qr_svg(
    content: str,
    foreground_color: str = "#000000",
    background_color: str = "#FFFFFF",
) -> str:
    """Generate an SVG QR code and return it as a string."""
    factory = qrcode.image.svg.SvgPathImage
    qr = qrcode.QRCode(
        error_correction=qr_constants.ERROR_CORRECT_M,
        image_factory=factory,
        box_size=10,
        border=4,
    )
    qr.add_data(content)
    qr.make(fit=True)
    svg_image = qr.make_image(
        fill_color=foreground_color,
        back_color=background_color,
    )
    buf = io.BytesIO()
    svg_image.save(buf)
    return buf.getvalue().decode("utf-8")


def image_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    """Convert PIL image to bytes."""
    buf = io.BytesIO()
    if fmt.upper() == "PNG":
        img.save(buf, format="PNG", optimize=True)
    elif fmt.upper() == "JPEG":
        img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=95)
    buf.seek(0)
    return buf.getvalue()


def generate_qr_pdf(img: Image.Image, qr_name: str = "") -> bytes:
    """Wrap QR image in a minimal PDF."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4

    # Center QR on page
    qr_bytes = image_to_bytes(img)
    qr_buf = io.BytesIO(qr_bytes)
    qr_size_cm = 10 * cm
    x = (page_w - qr_size_cm) / 2
    y = (page_h - qr_size_cm) / 2

    c.drawInlineImage(Image.open(qr_buf), x, y, qr_size_cm, qr_size_cm)

    if qr_name:
        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(page_w / 2, y - 30, qr_name)

    c.save()
    buf.seek(0)
    return buf.getvalue()


def build_wifi_content(ssid: str, password: str, security: str = "WPA", hidden: bool = False) -> str:
    hidden_str = "true" if hidden else "false"
    return f"WIFI:T:{security};S:{ssid};P:{password};H:{hidden_str};;"


def build_vcard_content(
    name: str,
    phone: str = "",
    email: str = "",
    organization: str = "",
    url: str = "",
    address: str = "",
) -> str:
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{name}",
        f"N:{name};;;;",
    ]
    if phone:
        lines.append(f"TEL:{phone}")
    if email:
        lines.append(f"EMAIL:{email}")
    if organization:
        lines.append(f"ORG:{organization}")
    if url:
        lines.append(f"URL:{url}")
    if address:
        lines.append(f"ADR:;;{address};;;;")
    lines.append("END:VCARD")
    return "\n".join(lines)


def build_whatsapp_content(phone: str, message: str = "") -> str:
    base = f"https://wa.me/{phone.lstrip('+').replace(' ', '')}"
    if message:
        base += f"?text={urllib.parse.quote(message)}"
    return base


def build_sms_content(phone: str, message: str = "") -> str:
    return f"SMSTO:{phone}:{message}"


def build_email_content(to: str, subject: str = "", body: str = "") -> str:
    parts = [f"mailto:{to}"]
    params = []
    if subject:
        params.append(f"subject={subject}")
    if body:
        params.append(f"body={body}")
    if params:
        parts.append("?" + "&".join(params))
    return "".join(parts)


