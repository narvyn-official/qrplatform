from django import forms
from apps.barcodes.models import Barcode, BulkBarcodeJob
from apps.barcodes.utils import UNSUPPORTED_FORMATS


INPUT_CLASS = "input"


def supported_barcode_choices():
    return [
        (value, label)
        for value, label in Barcode.BarcodeFormat.choices
        if value not in UNSUPPORTED_FORMATS
    ]


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
        self.fields["barcode_format"].choices = supported_barcode_choices()
        for name, field in self.fields.items():
            if name in ("foreground_color", "background_color"):
                field.widget.attrs.setdefault("class", "h-11 w-full rounded-lg border border-surface-200 bg-white p-1")
            elif isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "rounded border-surface-300 text-primary-600 focus:ring-primary-500")
            elif not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault("class", INPUT_CLASS)

    def clean_barcode_format(self):
        fmt = self.cleaned_data["barcode_format"]
        if fmt in UNSUPPORTED_FORMATS:
            raise forms.ValidationError("UPC-E is not supported yet. Use UPC-A or Code 128.")
        return fmt

    def clean_content(self):
        return self.cleaned_data["content"].strip()


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["barcode_format"].choices = supported_barcode_choices()
        for field in self.fields.values():
            if isinstance(field.widget, forms.FileInput):
                field.widget.attrs.setdefault("class", "block w-full text-sm text-gray-600 file:mr-4 file:rounded-lg file:border-0 file:bg-primary-50 file:px-3 file:py-2 file:text-sm file:font-semibold file:text-primary-700 hover:file:bg-primary-100")
            else:
                field.widget.attrs.setdefault("class", INPUT_CLASS)
