"""
DRF serializers for the public API.
"""
from rest_framework import serializers
from apps.qrcodes.models import QRCode, QRCodeCampaign
from apps.qrcodes.services import assert_can_create_qr
from apps.qrcodes.validation import (
    MAX_LOGO_SIZE_RATIO,
    MAX_QR_SIZE,
    MIN_QR_SIZE,
    normalize_http_url,
    validate_hex_color,
)
from apps.barcodes.models import Barcode
from apps.analytics.models import DailyQRStats, GeoStats
from apps.accounts.models import UserProfile, APIKey
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

User = get_user_model()


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ["company", "website", "timezone", "phone", "bio", "brand_color"]


class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)
    active_plan_code = serializers.ReadOnlyField()
    plan_expires_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "role", "active_plan_code", "plan_expires_at", "is_email_verified", "date_joined", "profile"]
        read_only_fields = ["id", "email", "role", "active_plan_code", "plan_expires_at", "is_email_verified", "date_joined"]


# ── QR Code ───────────────────────────────────────────────────────────────────

class QRCodeListSerializer(serializers.ModelSerializer):
    redirect_url = serializers.ReadOnlyField()
    is_expired = serializers.ReadOnlyField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = QRCode
        fields = [
            "id", "short_code", "name", "qr_type", "status",
            "total_scans", "unique_scans", "last_scanned_at",
            "is_expired", "redirect_url", "image_url",
            "tags", "created_at", "updated_at",
        ]

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image_png and request:
            return request.build_absolute_uri(obj.image_png.url)
        return None


class QRCodeDetailSerializer(serializers.ModelSerializer):
    redirect_url = serializers.ReadOnlyField()
    is_expired = serializers.ReadOnlyField()
    encoded_content = serializers.ReadOnlyField()
    image_png_url = serializers.SerializerMethodField()
    image_pdf_url = serializers.SerializerMethodField()

    class Meta:
        model = QRCode
        fields = [
            "id", "short_code", "name", "qr_type", "status",
            "content", "destination_url",
            "foreground_color", "background_color",
            "dot_style", "corner_style", "outer_shape",
            "logo_size_ratio", "frame_text", "frame_color",
            "error_correction", "qr_size",
            "expires_at", "scan_limit",
            "is_password_protected",
            "total_scans", "unique_scans", "last_scanned_at",
            "is_expired", "redirect_url", "encoded_content",
            "image_png_url", "image_svg", "image_pdf_url",
            "tags", "campaign",
            "created_at", "updated_at",
        ]

    def get_image_png_url(self, obj):
        request = self.context.get("request")
        if obj.image_png and request:
            return request.build_absolute_uri(obj.image_png.url)
        return None

    def get_image_pdf_url(self, obj):
        request = self.context.get("request")
        if obj.image_pdf and request:
            return request.build_absolute_uri(obj.image_pdf.url)
        return None


class QRCodeCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = QRCode
        fields = [
            "name", "qr_type", "content", "destination_url",
            "campaign", "tags",
            "foreground_color", "background_color",
            "dot_style", "corner_style", "outer_shape",
            "logo_size_ratio", "frame_text", "frame_color",
            "error_correction", "qr_size",
            "expires_at", "scan_limit",
            "is_password_protected",
        ]

    def validate(self, attrs):
        user = self.context["request"].user
        limits = user.plan_limits
        if self.instance is None:
            assert_can_create_qr(user, serializers.ValidationError)

        qr_type = attrs.get("qr_type", self.instance.qr_type if self.instance else QRCode.QRType.URL)
        content = (attrs.get("content", self.instance.content if self.instance else "") or "").strip()
        destination_url = (attrs.get("destination_url", self.instance.destination_url if self.instance else "") or "").strip()
        content_is_changing = "content" in attrs or "qr_type" in attrs or self.instance is None
        destination_is_changing = "destination_url" in attrs or "qr_type" in attrs or self.instance is None
        if qr_type == QRCode.QRType.DYNAMIC:
            if not destination_url:
                raise serializers.ValidationError({"destination_url": "Required for dynamic QR codes."})
            if destination_is_changing:
                try:
                    destination_url = normalize_http_url(destination_url)
                except DjangoValidationError:
                    raise serializers.ValidationError({"destination_url": "Enter a valid http or https URL."})
                attrs["destination_url"] = destination_url
                attrs["content"] = destination_url
        elif qr_type == QRCode.QRType.URL:
            if not content:
                raise serializers.ValidationError({"content": "Website URL is required."})
            if content_is_changing:
                try:
                    attrs["content"] = normalize_http_url(content)
                except DjangoValidationError:
                    raise serializers.ValidationError({"content": "Enter a valid http or https URL."})

        if attrs.get("outer_shape") not in (None, QRCode.OuterShape.SQUARE, QRCode.OuterShape.ROUNDED) and not limits["custom_shapes"]:
            raise serializers.ValidationError({"outer_shape": "Custom QR shapes require a paid plan."})
        if attrs.get("scan_limit") and limits["max_scans"] > 0 and attrs["scan_limit"] > limits["max_scans"]:
            raise serializers.ValidationError({"scan_limit": f"Your current plan allows up to {limits['max_scans']:,} scans per QR."})
        if attrs.get("expires_at") and attrs["expires_at"] <= timezone.now():
            raise serializers.ValidationError({"expires_at": "Expiry must be in the future."})

        for field in ("foreground_color", "background_color", "frame_color"):
            if field in attrs:
                try:
                    attrs[field] = validate_hex_color(attrs[field], field)
                except DjangoValidationError:
                    raise serializers.ValidationError({field: "Use a valid hex color, for example #000000."})
        if attrs.get("foreground_color") and attrs.get("background_color") and attrs["foreground_color"] == attrs["background_color"]:
            raise serializers.ValidationError({"background_color": "Background color must be different from foreground color."})

        if attrs.get("qr_size") and not MIN_QR_SIZE <= attrs["qr_size"] <= MAX_QR_SIZE:
            raise serializers.ValidationError({"qr_size": f"QR size must be between {MIN_QR_SIZE} and {MAX_QR_SIZE} pixels."})
        if attrs.get("logo_size_ratio") and attrs["logo_size_ratio"] > MAX_LOGO_SIZE_RATIO:
            raise serializers.ValidationError({"logo_size_ratio": "Logo size must be 35% of the QR code or smaller."})
        return attrs

    def create(self, validated_data):
        user = self.context["request"].user
        qr = QRCode.objects.create(user=user, **validated_data)
        from apps.qrcodes.tasks import generate_qr_images
        generate_qr_images(qr)
        return qr


class QRCodeUpdateDestinationSerializer(serializers.Serializer):
    """Lightweight serializer for updating dynamic QR destination only."""
    destination_url = serializers.URLField(max_length=2000)


# ── Barcode ───────────────────────────────────────────────────────────────────

class BarcodeSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Barcode
        fields = [
            "id", "name", "barcode_format", "content",
            "foreground_color", "background_color",
            "show_text", "width", "height",
            "image_url", "image_svg",
            "tags", "created_at",
        ]
        read_only_fields = ["id", "image_url", "image_svg", "created_at"]

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image_png and request:
            return request.build_absolute_uri(obj.image_png.url)
        return None

    def validate_content(self, value):
        from apps.barcodes.utils import validate_barcode_content
        barcode_format = self.initial_data.get("barcode_format", "code128")
        is_valid, error = validate_barcode_content(value, barcode_format)
        if not is_valid:
            raise serializers.ValidationError(error)
        return value

    def create(self, validated_data):
        from apps.barcodes.utils import generate_barcode_png, generate_barcode_svg
        from django.core.files.base import ContentFile

        user = self.context["request"].user
        barcode_obj = Barcode.objects.create(user=user, **validated_data)

        # Generate synchronously for API (fast enough)
        png = generate_barcode_png(
            barcode_obj.content,
            barcode_obj.barcode_format,
            barcode_obj.foreground_color,
            barcode_obj.background_color,
            barcode_obj.width,
            barcode_obj.height,
            barcode_obj.font_size,
            barcode_obj.show_text,
        )
        barcode_obj.image_png.save(f"{barcode_obj.id}.png", ContentFile(png), save=False)

        svg = generate_barcode_svg(
            barcode_obj.content,
            barcode_obj.barcode_format,
            barcode_obj.foreground_color,
            barcode_obj.background_color,
        )
        barcode_obj.image_svg = svg
        barcode_obj.save(update_fields=["image_png", "image_svg"])

        return barcode_obj


# ── Analytics ─────────────────────────────────────────────────────────────────

class DailyStatsSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyQRStats
        fields = [
            "date", "total_scans", "unique_scans",
            "mobile_scans", "desktop_scans", "tablet_scans",
            "country_breakdown", "browser_breakdown", "os_breakdown",
        ]


class GeoStatsSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeoStats
        fields = ["country_code", "country_name", "city", "scans"]


class AnalyticsSummarySerializer(serializers.Serializer):
    total_scans = serializers.IntegerField()
    unique_scans = serializers.IntegerField()
    mobile_percent = serializers.FloatField()
    desktop_percent = serializers.FloatField()
    top_countries = GeoStatsSerializer(many=True)
    daily_stats = DailyStatsSerializer(many=True)


# ── API Key ───────────────────────────────────────────────────────────────────

class APIKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = APIKey
        fields = ["id", "name", "key_prefix", "scopes", "status", "last_used_at", "created_at"]
        read_only_fields = ["id", "key_prefix", "last_used_at", "created_at"]


class APIKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=["qr:read", "qr:write", "analytics:read", "barcode:read", "barcode:write"]),
        default=["qr:read", "qr:write"],
    )
