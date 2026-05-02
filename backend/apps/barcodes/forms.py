from django import forms
from apps.barcodes.models import Barcode, BulkBarcodeJob


class BarcodeForm(forms.ModelForm):
    class Meta:
        model = Barcode
        fields = [
            "name", "barcode_format", "content",
            "foreground_color", "background_color",
            "show_text", "width", "height", "font_size", "tags",
        ]
        widgets = {
            "foreground_color": forms.TextInput(attrs={"type": "color"}),
            "background_color": forms.TextInput(attrs={"type": "color"}),
            "tags": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tags"].required = False


class BulkBarcodeForm(forms.ModelForm):
    class Meta:
        model = BulkBarcodeJob
        fields = ["name", "barcode_format", "source_file"]

    def clean_source_file(self):
        f = self.cleaned_data.get("source_file")
        if f:
            if not f.name.endswith(".csv"):
                from django.core.exceptions import ValidationError
                raise ValidationError("Upload must be a CSV file.")
            if f.size > 5 * 1024 * 1024:
                from django.core.exceptions import ValidationError
                raise ValidationError("CSV file must be under 5MB.")
        return f
