"""
Barcode models.
"""
import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

User = get_user_model()


def barcode_image_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1]
    return f"barcodes/{instance.user.id}/{instance.id}.{ext}"


class Barcode(models.Model):

    class BarcodeFormat(models.TextChoices):
        CODE128 = "code128", _("Code 128")
        CODE39 = "code39", _("Code 39")
        EAN13 = "ean13", _("EAN-13")
        EAN8 = "ean8", _("EAN-8")
        UPCA = "upca", _("UPC-A")
        UPCE = "upce", _("UPC-E")
        ITF = "itf", _("ITF / Interleaved 2 of 5")
        GS1_128 = "gs1_128", _("GS1-128")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="barcodes", db_index=True)
    name = models.CharField(max_length=200)
    barcode_format = models.CharField(max_length=20, choices=BarcodeFormat.choices, default=BarcodeFormat.CODE128)
    content = models.CharField(max_length=1000)

    # Design
    foreground_color = models.CharField(max_length=7, default="#000000")
    background_color = models.CharField(max_length=7, default="#FFFFFF")
    show_text = models.BooleanField(default=True)
    width = models.PositiveIntegerField(default=300)
    height = models.PositiveIntegerField(default=100)
    font_size = models.PositiveIntegerField(default=10)

    # Generated
    image_png = models.ImageField(upload_to=barcode_image_path, null=True, blank=True)
    image_svg = models.TextField(blank=True)

    # Bulk job reference
    bulk_job = models.ForeignKey(
        "BulkBarcodeJob", on_delete=models.SET_NULL, null=True, blank=True, related_name="barcodes"
    )

    tags = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "barcodes_barcode"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "barcode_format"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"Barcode({self.name} — {self.barcode_format})"


class BulkBarcodeJob(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bulk_barcode_jobs")
    name = models.CharField(max_length=200)
    barcode_format = models.CharField(max_length=20, choices=Barcode.BarcodeFormat.choices)
    source_file = models.FileField(upload_to="bulk_jobs/csv/")
    output_zip = models.FileField(upload_to="bulk_jobs/output/", null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    total_count = models.PositiveIntegerField(default=0)
    processed_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    error_log = models.JSONField(default=list)
    celery_task_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "barcodes_bulk_job"
        ordering = ["-created_at"]

    def __str__(self):
        return f"BulkJob({self.name} — {self.status})"

    @property
    def progress_percent(self):
        if self.total_count == 0:
            return 0
        return round((self.processed_count / self.total_count) * 100)
