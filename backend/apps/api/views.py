"""
DRF API viewsets — rate-limited, authenticated, scoped.
"""
import logging
from django.utils import timezone
from django.db.models import Sum, Count, Q
from rest_framework import viewsets, status, generics
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.throttling import UserRateThrottle
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from drf_spectacular.utils import extend_schema, OpenApiParameter

from apps.qrcodes.models import QRCode, QRCodeCampaign
from apps.barcodes.models import Barcode, BulkBarcodeJob
from apps.analytics.models import DailyQRStats, GeoStats
from apps.api.serializers import (
    QRCodeListSerializer, QRCodeDetailSerializer, QRCodeCreateSerializer,
    QRCodeUpdateDestinationSerializer,
    BarcodeSerializer,
    DailyStatsSerializer, GeoStatsSerializer, AnalyticsSummarySerializer,
    APIKeySerializer, APIKeyCreateSerializer,
    UserSerializer,
)
from apps.api.permissions import HasAPIKeyScope
from apps.api.throttling import QRGenerationThrottle

logger = logging.getLogger(__name__)


# ── QR Code ViewSet ───────────────────────────────────────────────────────────

class QRCodeViewSet(viewsets.ModelViewSet):
    """
    CRUD for QR codes. Supports search, filter, ordering.
    """
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["qr_type", "status", "campaign"]
    search_fields = ["name", "tags"]
    ordering_fields = ["created_at", "total_scans", "name"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return QRCodeListSerializer
        if self.action in ("create", "update", "partial_update"):
            return QRCodeCreateSerializer
        return QRCodeDetailSerializer

    def get_queryset(self):
        return QRCode.objects.filter(
            user=self.request.user
        ).exclude(status="deleted").select_related("campaign")

    def get_throttles(self):
        if self.action == "create":
            return [QRGenerationThrottle()]
        return super().get_throttles()

    def perform_create(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        instance.status = QRCode.Status.DELETED
        instance.save(update_fields=["status"])

    @extend_schema(
        request=QRCodeUpdateDestinationSerializer,
        responses={200: QRCodeDetailSerializer},
        description="Update the destination URL of a dynamic QR code without changing the QR image.",
    )
    @action(detail=True, methods=["patch"], url_path="destination")
    def update_destination(self, request, pk=None):
        """Update dynamic QR destination URL — QR image stays the same."""
        qr = self.get_object()
        if not qr.is_dynamic:
            return Response(
                {"detail": "Only dynamic QR codes support destination updates."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = QRCodeUpdateDestinationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        qr.destination_url = serializer.validated_data["destination_url"]
        qr.save(update_fields=["destination_url", "updated_at"])
        return Response(QRCodeDetailSerializer(qr, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="pause")
    def pause(self, request, pk=None):
        qr = self.get_object()
        qr.status = QRCode.Status.PAUSED
        qr.save(update_fields=["status"])
        return Response({"status": "paused"})

    @action(detail=True, methods=["post"], url_path="resume")
    def resume(self, request, pk=None):
        qr = self.get_object()
        qr.status = QRCode.Status.ACTIVE
        qr.save(update_fields=["status"])
        return Response({"status": "active"})

    @action(detail=True, methods=["get"], url_path="analytics")
    def analytics(self, request, pk=None):
        """Per-QR analytics summary."""
        qr = self.get_object()
        days = int(request.query_params.get("days", 30))
        from datetime import timedelta
        cutoff = (timezone.now() - timedelta(days=days)).date()

        daily = DailyQRStats.objects.filter(qrcode_id=qr.id, date__gte=cutoff).order_by("date")
        geo = GeoStats.objects.filter(qrcode_id=qr.id).order_by("-scans")[:20]

        total_scans = qr.total_scans
        unique_scans = qr.unique_scans
        daily_totals = daily.aggregate(
            mob=Sum("mobile_scans"), desk=Sum("desktop_scans")
        )
        mobile = daily_totals.get("mob") or 0
        desktop = daily_totals.get("desk") or 0
        total_device = mobile + desktop or 1

        data = {
            "total_scans": total_scans,
            "unique_scans": unique_scans,
            "mobile_percent": round(mobile / total_device * 100, 1),
            "desktop_percent": round(desktop / total_device * 100, 1),
            "top_countries": GeoStatsSerializer(geo, many=True).data,
            "daily_stats": DailyStatsSerializer(daily, many=True).data,
        }
        return Response(data)


# ── Barcode ViewSet ───────────────────────────────────────────────────────────

class BarcodeViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BarcodeSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["barcode_format"]
    search_fields = ["name", "content"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return Barcode.objects.filter(user=self.request.user)

    @action(detail=False, methods=["post"], url_path="bulk-validate")
    def bulk_validate(self, request):
        """Validate a list of barcode content strings before bulk job."""
        from apps.barcodes.utils import validate_barcode_content
        items = request.data.get("items", [])
        fmt = request.data.get("format", "code128")
        results = []
        for item in items[:1000]:  # hard cap
            ok, err = validate_barcode_content(str(item), fmt)
            results.append({"content": item, "valid": ok, "error": err})
        return Response({"results": results})


# ── Analytics ViewSet ─────────────────────────────────────────────────────────

class AnalyticsViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """Account-level analytics summary."""
        from datetime import timedelta
        from apps.analytics.models import UserDailyStats

        days = int(request.query_params.get("days", 30))
        cutoff = (timezone.now() - timedelta(days=days)).date()

        stats = UserDailyStats.objects.filter(
            user_id=request.user.id, date__gte=cutoff
        ).order_by("date")

        total_scans = sum(s.total_scans for s in stats)
        unique_scans = sum(s.unique_scans for s in stats)
        active_qr = QRCode.objects.filter(user=request.user, status="active").count()

        return Response({
            "total_scans": total_scans,
            "unique_scans": unique_scans,
            "active_qr_count": active_qr,
            "daily_stats": [
                {"date": str(s.date), "total_scans": s.total_scans, "unique_scans": s.unique_scans}
                for s in stats
            ],
        })

    @action(detail=False, methods=["get"], url_path="export")
    def export_csv(self, request):
        """Export analytics as CSV."""
        import csv
        from django.http import StreamingHttpResponse
        from apps.qrcodes.models import QRScanEvent

        qrcode_id = request.query_params.get("qrcode_id")
        if not qrcode_id:
            return Response({"detail": "qrcode_id is required."}, status=400)

        qr = QRCode.objects.filter(id=qrcode_id, user=request.user).first()
        if not qr:
            return Response({"detail": "Not found."}, status=404)

        def generate_rows():
            yield ["timestamp", "country", "city", "device", "os", "browser", "is_unique"]
            for event in QRScanEvent.objects.filter(
                qrcode=qr, is_processed=True
            ).values(
                "timestamp", "country_name", "city", "device_type", "os_family", "browser_family", "is_unique"
            ).iterator():
                yield [
                    event["timestamp"].isoformat(),
                    event["country_name"],
                    event["city"],
                    event["device_type"],
                    event["os_family"],
                    event["browser_family"],
                    event["is_unique"],
                ]

        class EchoPseudoBuffer:
            def write(self, value): return value

        pseudo_buffer = EchoPseudoBuffer()
        writer = csv.writer(pseudo_buffer)
        response = StreamingHttpResponse(
            (writer.writerow(row) for row in generate_rows()),
            content_type="text/csv",
        )
        response["Content-Disposition"] = f'attachment; filename="analytics_{qr.short_code}.csv"'
        return response


# ── User ViewSet ──────────────────────────────────────────────────────────────

class UserViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        serializer = UserSerializer(request.user, context={"request": request})
        return Response(serializer.data)

    @action(detail=False, methods=["patch"], url_path="me/update")
    def update_me(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


# ── API Key management ────────────────────────────────────────────────────────

class APIKeyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        from apps.accounts.models import APIKey
        keys = APIKey.objects.filter(user=request.user).order_by("-created_at")
        return Response(APIKeySerializer(keys, many=True).data)

    def create(self, request):
        from apps.accounts.models import APIKey
        serializer = APIKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if APIKey.objects.filter(user=request.user, status="active").count() >= 10:
            return Response({"detail": "Maximum 10 active API keys."}, status=400)

        key_obj, raw_key = APIKey.generate(
            request.user,
            serializer.validated_data["name"],
            serializer.validated_data.get("scopes"),
        )
        return Response({
            **APIKeySerializer(key_obj).data,
            "raw_key": raw_key,  # shown ONCE — never stored
        }, status=201)

    @action(detail=True, methods=["delete"], url_path="revoke")
    def revoke(self, request, pk=None):
        from apps.accounts.models import APIKey
        try:
            key = APIKey.objects.get(id=pk, user=request.user)
        except APIKey.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        key.status = "revoked"
        key.save(update_fields=["status"])
        return Response({"status": "revoked"})
