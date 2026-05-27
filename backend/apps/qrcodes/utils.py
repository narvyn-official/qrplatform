"""
QR Code generation engine — supports customization, logos, and multiple formats.
"""
import io
import os
import logging
import base64
import urllib.parse
from typing import Optional, Tuple

import qrcode
from qrcode import constants as qr_constants
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import qrcode.image.svg

logger = logging.getLogger(__name__)

DOT_STYLES = {"square", "rounded", "dots", "classy"}
CORNER_STYLES = {"square", "extra_rounded", "dot"}

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
    corner_style: str = "square",
    error_correction: str = "M",
    size: int = 300,
    logo_path: Optional[str] = None,
    logo_size_ratio: float = 0.2,
    frame_text: str = "",
    frame_color: str = "#000000",
    outer_shape: str = "square",
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

    qr = qrcode.QRCode(
        version=None,  # auto-size
        error_correction=ec_level,
        box_size=10,
        border=4,
    )
    qr.add_data(content)
    qr.make(fit=True)

    shape = outer_shape or "square"
    qr_core_size = size if shape == "square" else max(150, int(size * 0.64))

    img = _draw_styled_qr(
        qr.modules,
        size=qr_core_size,
        fg_rgb=fg_rgb,
        bg_rgb=bg_rgb,
        dot_style=dot_style,
        corner_style=corner_style,
    )

    # Embed logo if provided
    if logo_path and os.path.exists(logo_path):
        img = _embed_logo(img, logo_path, logo_size_ratio)

    if shape != "square":
        img = _apply_outer_shape(img, shape, size, background_color, foreground_color)

    # Add frame / caption below QR
    if frame_text:
        img = _add_frame_text(img, frame_text, frame_color, background_color)

    return img


def _normalize_dot_style(dot_style: str) -> str:
    if dot_style in {"classy_rounded", "extra_rounded"}:
        return "rounded"
    return dot_style if dot_style in DOT_STYLES else "square"


def _normalize_corner_style(corner_style: str) -> str:
    return corner_style if corner_style in CORNER_STYLES else "square"


def _finder_origins(module_count: int):
    return (
        (0, 0),
        (0, module_count - 7),
        (module_count - 7, 0),
    )


def _is_finder_module(row: int, col: int, module_count: int) -> bool:
    return any(
        origin_row <= row < origin_row + 7 and origin_col <= col < origin_col + 7
        for origin_row, origin_col in _finder_origins(module_count)
    )


def _module_box(row: int, col: int, border: int, module_size: float):
    x0 = (col + border) * module_size
    y0 = (row + border) * module_size
    return x0, y0, x0 + module_size, y0 + module_size


def _draw_data_module(draw: ImageDraw.ImageDraw, box, style: str, fill):
    x0, y0, x1, y1 = box
    module_size = x1 - x0
    if style == "dots":
        inset = module_size * 0.11
        draw.ellipse((x0 + inset, y0 + inset, x1 - inset, y1 - inset), fill=fill)
    elif style == "rounded":
        inset = module_size * 0.04
        draw.rounded_rectangle(
            (x0 + inset, y0 + inset, x1 - inset, y1 - inset),
            radius=module_size * 0.34,
            fill=fill,
        )
    elif style == "classy":
        inset = module_size * 0.12
        draw.rounded_rectangle(
            (x0 + inset, y0 + inset, x1 - inset, y1 - inset),
            radius=module_size * 0.12,
            fill=fill,
        )
    else:
        bleed = module_size * 0.01
        draw.rectangle((x0 - bleed, y0 - bleed, x1 + bleed, y1 + bleed), fill=fill)


def _draw_finder(draw: ImageDraw.ImageDraw, row: int, col: int, border: int, module_size: float, style: str, fg, bg):
    x0 = (col + border) * module_size
    y0 = (row + border) * module_size
    outer = (x0, y0, x0 + 7 * module_size, y0 + 7 * module_size)
    middle = (
        x0 + module_size,
        y0 + module_size,
        x0 + 6 * module_size,
        y0 + 6 * module_size,
    )
    center = (
        x0 + 2 * module_size,
        y0 + 2 * module_size,
        x0 + 5 * module_size,
        y0 + 5 * module_size,
    )

    if style == "dot":
        draw.ellipse(outer, fill=fg)
        draw.ellipse(middle, fill=bg)
        draw.ellipse(center, fill=fg)
    elif style == "extra_rounded":
        draw.rounded_rectangle(outer, radius=module_size * 1.55, fill=fg)
        draw.rounded_rectangle(middle, radius=module_size * 1.05, fill=bg)
        draw.rounded_rectangle(center, radius=module_size * 0.72, fill=fg)
    else:
        draw.rectangle(outer, fill=fg)
        draw.rectangle(middle, fill=bg)
        draw.rectangle(center, fill=fg)


def _draw_styled_qr(
    matrix,
    *,
    size: int,
    fg_rgb,
    bg_rgb,
    dot_style: str,
    corner_style: str,
) -> Image.Image:
    """Render a QR matrix with scan-safe module and finder styles."""
    module_count = len(matrix)
    border = 4
    render_size = max(size * 4, (module_count + border * 2) * 12)
    module_size = render_size / (module_count + border * 2)
    fg = tuple(fg_rgb) + (255,)
    bg = tuple(bg_rgb) + (255,)
    normalized_dot = _normalize_dot_style(dot_style)
    normalized_corner = _normalize_corner_style(corner_style)

    img = Image.new("RGBA", (render_size, render_size), bg)
    draw = ImageDraw.Draw(img, "RGBA")

    for row, modules in enumerate(matrix):
        for col, is_dark in enumerate(modules):
            if not is_dark or _is_finder_module(row, col, module_count):
                continue
            _draw_data_module(
                draw,
                _module_box(row, col, border, module_size),
                normalized_dot,
                fg,
            )

    for row, col in _finder_origins(module_count):
        _draw_finder(draw, row, col, border, module_size, normalized_corner, fg, bg)

    return img.resize((size, size), Image.LANCZOS)


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


def _rgba(hex_color: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    return hex_to_rgb(hex_color) + (alpha,)


def _shape_bbox(size: int, margin_ratio: float = 0.035) -> Tuple[int, int, int, int]:
    margin = max(4, int(size * margin_ratio))
    return margin, margin, size - margin, size - margin


def _draw_shape(draw: ImageDraw.ImageDraw, shape: str, size: int, fill, outline) -> None:
    bbox = _shape_bbox(size)
    if shape == "rounded":
        draw.rounded_rectangle(bbox, radius=int(size * 0.18), fill=fill, outline=outline, width=max(2, size // 120))
    elif shape == "circle":
        draw.ellipse(bbox, fill=fill, outline=outline, width=max(2, size // 120))
    elif shape == "capsule":
        x0, y0, x1, y1 = bbox
        inset = int(size * 0.08)
        draw.rounded_rectangle((x0 + inset, y0, x1 - inset, y1), radius=int(size * 0.32), fill=fill, outline=outline, width=max(2, size // 120))
    elif shape == "ticket":
        draw.rounded_rectangle(bbox, radius=int(size * 0.1), fill=fill, outline=outline, width=max(2, size // 120))
        notch = int(size * 0.075)
        for x, y in ((bbox[0], size // 2), (bbox[2], size // 2)):
            draw.ellipse((x - notch, y - notch, x + notch, y + notch), fill=(0, 0, 0, 0))
    elif shape == "apple":
        x0, y0, x1, y1 = bbox
        draw.ellipse((x0 + int(size * .08), y0 + int(size * .17), x1 - int(size * .08), y1), fill=fill, outline=outline, width=max(2, size // 120))
        draw.ellipse((x0 + int(size * .16), y0 + int(size * .05), x0 + int(size * .52), y0 + int(size * .42)), fill=fill)
        draw.ellipse((x1 - int(size * .52), y0 + int(size * .05), x1 - int(size * .16), y0 + int(size * .42)), fill=fill)
        draw.polygon([(size * .53, size * .1), (size * .66, size * .02), (size * .75, size * .12), (size * .61, size * .18)], fill=_rgba("#10b981", 220))
        draw.line([(size * .52, size * .17), (size * .48, size * .08)], fill=outline, width=max(2, size // 90))
    elif shape == "duck":
        x0, y0, x1, y1 = bbox
        draw.ellipse((x0 + int(size * .08), y0 + int(size * .28), x1 - int(size * .02), y1 - int(size * .05)), fill=fill, outline=outline, width=max(2, size // 120))
        draw.ellipse((x0 + int(size * .08), y0 + int(size * .08), x0 + int(size * .42), y0 + int(size * .42)), fill=fill, outline=outline, width=max(2, size // 120))
        draw.polygon([(x0 + int(size * .38), y0 + int(size * .24)), (x0 + int(size * .55), y0 + int(size * .28)), (x0 + int(size * .39), y0 + int(size * .34))], fill=_rgba("#f59e0b", 230))
        eye = max(3, size // 80)
        cx, cy = x0 + int(size * .28), y0 + int(size * .22)
        draw.ellipse((cx - eye, cy - eye, cx + eye, cy + eye), fill=_rgba("#111827", 255))
    elif shape == "shield":
        points = [
            (size * .5, size * .03), (size * .88, size * .17),
            (size * .82, size * .62), (size * .5, size * .96),
            (size * .18, size * .62), (size * .12, size * .17),
        ]
        draw.polygon(points, fill=fill, outline=outline)
    else:
        points = [
            (size * .18, size * .13), (size * .62, size * .05),
            (size * .9, size * .34), (size * .82, size * .78),
            (size * .45, size * .95), (size * .08, size * .72),
            (size * .06, size * .32),
        ]
        draw.polygon(points, fill=fill, outline=outline)


def _apply_outer_shape(
    qr_img: Image.Image,
    shape: str,
    size: int,
    bg_color: str,
    fg_color: str,
) -> Image.Image:
    """Place a complete QR core inside a decorative, scan-safe outer shell."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow, "RGBA")
    _draw_shape(shadow_draw, shape, size, _rgba("#0f172a", 28), _rgba("#0f172a", 0))
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    canvas.alpha_composite(shadow)

    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    _draw_shape(draw, shape, size, _rgba(bg_color, 255), _rgba(fg_color, 46))
    canvas.alpha_composite(layer)

    card_padding = max(6, int(size * .025))
    card_size = qr_img.size[0] + card_padding * 2
    card = Image.new("RGBA", (card_size, card_size), (0, 0, 0, 0))
    card_draw = ImageDraw.Draw(card, "RGBA")
    card_draw.rounded_rectangle(
        (0, 0, card_size - 1, card_size - 1),
        radius=max(10, int(size * .055)),
        fill=_rgba(bg_color, 245),
        outline=_rgba(fg_color, 30),
        width=max(1, size // 220),
    )
    card.alpha_composite(qr_img, (card_padding, card_padding))

    pos = ((size - card_size) // 2, (size - card_size) // 2)
    canvas.alpha_composite(card, pos)
    return canvas


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


def image_to_svg(img: Image.Image, title: str = "QR Code") -> str:
    """Wrap the styled PNG output in an SVG so SVG downloads match PNG styling."""
    png = image_to_bytes(img, "PNG")
    encoded = base64.b64encode(png).decode("ascii")
    width, height = img.size
    safe_title = (title or "QR Code").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{safe_title}">'
        f"<title>{safe_title}</title>"
        f'<image width="{width}" height="{height}" href="data:image/png;base64,{encoded}"/>'
        "</svg>"
    )


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
