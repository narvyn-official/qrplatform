"""
Barcode views.
"""
import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.core.files.base import ContentFile

from apps.barcodes.models import Barcode, BulkBarcodeJob
from apps.barcodes.forms import BarcodeForm, BulkBarcodeForm
from apps.barcodes.utils import (
    generate_barcode_png, generate_barcode_svg, validate_barcode_content
)

logger = logging.getLogger(__name__)


@login_required
def barcode_list(request):
    qs = Barcode.objects.filter(user=request.user).order_by("-created_at")
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(request, "barcodes/list.html", {"barcodes": page, "active_tab": "barcodes"})


@login_required
def barcode_create(request):
    if request.method == "POST":
        form = BarcodeForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            is_valid, error = validate_barcode_content(data["content"], data["barcode_format"])
            if not is_valid:
                form.add_error("content", error)
            else:
                barcode_obj = form.save(commit=False)
                barcode_obj.user = request.user

                png = generate_barcode_png(
                    data["content"], data["barcode_format"],
                    data.get("foreground_color", "#000000"),
                    data.get("background_color", "#FFFFFF"),
                    data.get("width", 300), data.get("height", 100),
                    data.get("font_size", 10), data.get("show_text", True),
                )
                barcode_obj.image_png.save(f"{barcode_obj.id}.png", ContentFile(png), save=False)
                barcode_obj.image_svg = generate_barcode_svg(
                    data["content"], data["barcode_format"],
                    data.get("foreground_color", "#000000"),
                    data.get("background_color", "#FFFFFF"),
                    data.get("width", 300), data.get("height", 100),
                    data.get("show_text", True),
                )
                barcode_obj.save()
                messages.success(request, "Barcode created!")
                return redirect("barcodes:detail", pk=barcode_obj.id)
    else:
        form = BarcodeForm()

    return render(request, "barcodes/create.html", {
        "form": form, "active_tab": "create_barcode",
        "formats": Barcode.BarcodeFormat.choices,
    })


@login_required
def barcode_detail(request, pk):
    bc = get_object_or_404(Barcode, id=pk, user=request.user)
    return render(request, "barcodes/detail.html", {"barcode": bc, "active_tab": "barcodes"})


@login_required
@require_POST
def barcode_delete(request, pk):
    bc = get_object_or_404(Barcode, id=pk, user=request.user)
    bc.delete()
    messages.success(request, "Barcode deleted.")
    return redirect("barcodes:list")


@login_required
def barcode_download(request, pk, fmt):
    bc = get_object_or_404(Barcode, id=pk, user=request.user)
    if fmt == "png":
        if not bc.image_png:
            return HttpResponse("Not generated.", status=202)
        response = HttpResponse(bc.image_png.read(), content_type="image/png")
        response["Content-Disposition"] = f'attachment; filename="{bc.id}.png"'
    elif fmt == "svg":
        if not bc.image_svg:
            return HttpResponse("Not generated.", status=202)
        response = HttpResponse(bc.image_svg, content_type="image/svg+xml")
        response["Content-Disposition"] = f'attachment; filename="{bc.id}.svg"'
    else:
        return HttpResponse("Invalid format.", status=400)
    return response


@login_required
def bulk_barcode(request):
    if request.method == "POST":
        form = BulkBarcodeForm(request.POST, request.FILES)
        if form.is_valid():
            job = form.save(commit=False)
            job.user = request.user
            job.save()
            from apps.barcodes.tasks import process_bulk_barcode_job
            process_bulk_barcode_job.delay(str(job.id))
            messages.success(request, "Bulk job queued! You'll be notified when done.")
            return redirect("barcodes:list")
    else:
        form = BulkBarcodeForm()

    jobs = BulkBarcodeJob.objects.filter(user=request.user).order_by("-created_at")[:5]
    return render(request, "barcodes/bulk.html", {
        "form": form, "jobs": jobs, "active_tab": "barcodes"
    })
