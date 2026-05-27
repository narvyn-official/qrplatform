"""
QR Code async tasks — image generation and expiration cleanup.
"""
import logging

from celery import shared_task
from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def generate_qr_images_task(self, qrcode_id: str):
    """
    Generate PNG, SVG, and PDF variants for a QR code asynchronously.
    Called after QR creation/update.
    """
    from apps.qrcodes.models import QRCode
    try:
        qr = QRCode.objects.get(id=qrcode_id)
    except QRCode.DoesNotExist:
        logger.error("QRCode %s not found for image generation", qrcode_id)
        return

    try:
        generate_qr_images(qr)
        logger.info("Generated images for QRCode %s", qr.short_code)

    except Exception as exc:
        logger.exception("Image generation failed for QRCode %s: %s", qrcode_id, exc)
        raise self.retry(exc=exc)


def generate_qr_images(qr):
    """Generate and persist PNG, SVG, and PDF files for one QR code."""
    from apps.qrcodes.utils import (
        generate_qr_image,
        generate_qr_pdf,
        image_to_bytes,
        image_to_svg,
    )

    logo_path = qr.logo.path if qr.logo else None

    img = generate_qr_image(
        content=qr.encoded_content,
        foreground_color=qr.foreground_color,
        background_color=qr.background_color,
        dot_style=qr.dot_style,
        corner_style=qr.corner_style,
        error_correction=qr.error_correction,
        size=qr.qr_size,
        logo_path=logo_path,
        logo_size_ratio=qr.logo_size_ratio,
        frame_text=qr.frame_text,
        frame_color=qr.frame_color,
        outer_shape=qr.outer_shape,
    )

    png_bytes = image_to_bytes(img, "PNG")
    qr.image_png.save(
        f"{qr.short_code}.png",
        ContentFile(png_bytes),
        save=False,
    )

    qr.image_svg = image_to_svg(img, qr.name)

    pdf_bytes = generate_qr_pdf(img, qr.name)
    qr.image_pdf.save(
        f"{qr.short_code}.pdf",
        ContentFile(pdf_bytes),
        save=False,
    )

    qr.save(update_fields=["image_png", "image_svg", "image_pdf", "updated_at"])
    return qr


@shared_task
def cleanup_expired_qrcodes():
    """Mark expired QR codes and send scan-limit notifications."""
    from apps.qrcodes.models import QRCode

    # Expire time-based
    expired_time = QRCode.objects.filter(
        status=QRCode.Status.ACTIVE,
        expires_at__lt=timezone.now(),
    )
    count_time = expired_time.update(status=QRCode.Status.EXPIRED)

    # Expire scan-limit based
    from django.db.models import F
    expired_limit = QRCode.objects.filter(
        status=QRCode.Status.ACTIVE,
        scan_limit__isnull=False,
        total_scans__gte=F("scan_limit"),
    )
    count_limit = expired_limit.update(status=QRCode.Status.EXPIRED)

    logger.info("Expired %d time-based and %d scan-limit QR codes", count_time, count_limit)
