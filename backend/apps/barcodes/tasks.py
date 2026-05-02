"""
Barcode async tasks.
"""
import csv
import io
import zipfile
import logging
from celery import shared_task
from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2)
def process_bulk_barcode_job(self, job_id: str):
    from apps.barcodes.models import BulkBarcodeJob, Barcode
    from apps.barcodes.utils import generate_barcode_png, validate_barcode_content

    try:
        job = BulkBarcodeJob.objects.get(id=job_id)
    except BulkBarcodeJob.DoesNotExist:
        return

    job.status = BulkBarcodeJob.Status.PROCESSING
    job.save(update_fields=["status"])

    zip_buffer = io.BytesIO()
    errors = []

    try:
        with open(job.source_file.path, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

        job.total_count = len(rows)
        job.save(update_fields=["total_count"])

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, row in enumerate(rows):
                content = row.get("content", "").strip()
                name = row.get("name", f"barcode_{i+1}").strip()

                is_valid, error = validate_barcode_content(content, job.barcode_format)
                if not is_valid:
                    errors.append({"row": i + 1, "content": content, "error": error})
                    job.failed_count += 1
                    continue

                try:
                    png = generate_barcode_png(content, job.barcode_format)
                    bc = Barcode.objects.create(
                        user=job.user,
                        name=name,
                        barcode_format=job.barcode_format,
                        content=content,
                        bulk_job=job,
                    )
                    bc.image_png.save(f"{bc.id}.png", ContentFile(png), save=True)
                    zf.writestr(f"{name}_{i+1}.png", png)
                    job.processed_count += 1
                except Exception as exc:
                    errors.append({"row": i + 1, "content": content, "error": str(exc)})
                    job.failed_count += 1

                if i % 10 == 0:
                    job.save(update_fields=["processed_count", "failed_count"])

        zip_buffer.seek(0)
        job.output_zip.save(f"bulk_{job_id}.zip", ContentFile(zip_buffer.read()), save=False)
        job.status = BulkBarcodeJob.Status.COMPLETED
        job.error_log = errors
        job.completed_at = timezone.now()
        job.save()

    except Exception as exc:
        logger.exception("Bulk barcode job %s failed: %s", job_id, exc)
        job.status = BulkBarcodeJob.Status.FAILED
        job.error_log = [{"error": str(exc)}]
        job.save(update_fields=["status", "error_log"])
        raise self.retry(exc=exc)
