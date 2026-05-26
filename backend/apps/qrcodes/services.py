"""Application services for QR code workflows."""
from apps.qrcodes.models import QRCode


ACTIVE_QUOTA_STATUSES = (QRCode.Status.ACTIVE, QRCode.Status.PAUSED)


def active_qr_count(user):
    return QRCode.objects.filter(user=user, status__in=ACTIVE_QUOTA_STATUSES).count()


def can_create_qr(user):
    max_qr = user.plan_limits["max_qr"]
    return max_qr < 0 or active_qr_count(user) < max_qr


def assert_can_create_qr(user, validation_error_cls, field="plan"):
    max_qr = user.plan_limits["max_qr"]
    if max_qr > 0 and active_qr_count(user) >= max_qr:
        raise validation_error_cls({field: f"Your current plan allows {max_qr} active QR codes."})


def qr_quota_message(user, action="create"):
    max_qr = user.plan_limits["max_qr"]
    return f"Your current plan allows {max_qr} active QR codes. Upgrade to {action} more."
