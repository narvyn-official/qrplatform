"""Business QR template catalogue and payload builders."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlencode

from django.core.exceptions import ValidationError

from apps.qrcodes.models import QRCode
from apps.qrcodes.utils import build_email_content, build_sms_content, build_whatsapp_content
from apps.qrcodes.validation import EMAIL_VALIDATOR, normalize_http_url, validate_phone_number


PLAN_ORDER = {"free": 0, "pro": 1, "enterprise": 2}
TEMPLATE_CATEGORIES = (
    "Business",
    "Restaurant",
    "Payments",
    "Events",
    "Education",
    "Products",
    "Marketing",
    "Personal",
    "Utilities",
)


@dataclass(frozen=True)
class TemplateField:
    name: str
    label: str
    kind: str = "text"
    required: bool = True
    placeholder: str = ""
    help_text: str = ""
    choices: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class QRTemplate:
    slug: str
    title: str
    category: str
    description: str
    plan: str
    qr_type: str
    fields: tuple[TemplateField, ...]
    icon: str = "qr"

    @property
    def plan_label(self):
        return {"free": "Free", "pro": "Pro", "enterprise": "Business"}.get(self.plan, "Free")


def _field(*args, **kwargs):
    return TemplateField(*args, **kwargs)


TEMPLATES = (
    QRTemplate(
        "website-url",
        "Website URL",
        "Business",
        "Send scanners to any website, landing page, booking page, or campaign URL.",
        "free",
        QRCode.QRType.DYNAMIC,
        (_field("url", "Website URL", "url", placeholder="https://example.com"),),
        "link",
    ),
    QRTemplate(
        "restaurant-menu",
        "Restaurant menu",
        "Restaurant",
        "Create an editable menu QR for tables, takeaway counters, and food packaging.",
        "free",
        QRCode.QRType.DYNAMIC,
        (
            _field("restaurant_name", "Restaurant name", placeholder="Narvyn Cafe"),
            _field("menu_url", "Menu URL", "url", placeholder="https://example.com/menu"),
        ),
        "menu",
    ),
    QRTemplate(
        "upi-payment",
        "UPI payment",
        "Payments",
        "Let customers scan and pay with a clear UPI payee name and optional amount.",
        "free",
        QRCode.QRType.TEXT,
        (
            _field("payee_vpa", "UPI ID / VPA", placeholder="merchant@upi"),
            _field("payee_name", "Payee name", placeholder="Business name"),
            _field("amount", "Amount", "number", required=False, placeholder="499.00"),
            _field("note", "Payment note", required=False, placeholder="Order payment"),
        ),
        "payment",
    ),
    QRTemplate(
        "invoice-payment",
        "Invoice payment",
        "Payments",
        "Generate a payment QR for a specific invoice number and amount.",
        "pro",
        QRCode.QRType.TEXT,
        (
            _field("invoice_number", "Invoice number", placeholder="INV-1024"),
            _field("payee_vpa", "UPI ID / VPA", placeholder="accounts@upi"),
            _field("payee_name", "Payee name", placeholder="Company name"),
            _field("amount", "Invoice amount", "number", placeholder="1299.00"),
        ),
        "invoice",
    ),
    QRTemplate(
        "event-ticket",
        "Event ticket",
        "Events",
        "Link attendees to a ticket, RSVP, or event check-in page.",
        "pro",
        QRCode.QRType.DYNAMIC,
        (
            _field("event_name", "Event name", placeholder="Startup meetup"),
            _field("ticket_url", "Ticket or RSVP URL", "url", placeholder="https://example.com/ticket"),
        ),
        "ticket",
    ),
    QRTemplate(
        "attendance",
        "Attendance",
        "Education",
        "Open a school, class, workshop, or event attendance form.",
        "pro",
        QRCode.QRType.DYNAMIC,
        (
            _field("session_name", "Session name", placeholder="Class 10 science"),
            _field("attendance_url", "Attendance form URL", "url", placeholder="https://forms.example.com/attendance"),
        ),
        "attendance",
    ),
    QRTemplate(
        "product-page",
        "Product page",
        "Products",
        "Send packaging scans to product details, specs, warranty, or support.",
        "pro",
        QRCode.QRType.DYNAMIC,
        (
            _field("product_name", "Product name", placeholder="Herbal Shampoo 250ml"),
            _field("product_url", "Product page URL", "url", placeholder="https://example.com/products/shampoo"),
        ),
        "product",
    ),
    QRTemplate(
        "feedback-form",
        "Feedback form",
        "Marketing",
        "Collect customer feedback from receipts, packaging, tables, or events.",
        "pro",
        QRCode.QRType.DYNAMIC,
        (_field("feedback_url", "Feedback form URL", "url", placeholder="https://forms.example.com/feedback"),),
        "feedback",
    ),
    QRTemplate(
        "coupon",
        "Coupon",
        "Marketing",
        "Launch a redeemable coupon QR for offers, posters, and campaigns.",
        "pro",
        QRCode.QRType.DYNAMIC,
        (
            _field("coupon_code", "Coupon code", placeholder="SAVE20"),
            _field("offer_url", "Offer / redemption URL", "url", placeholder="https://example.com/offer"),
        ),
        "coupon",
    ),
    QRTemplate(
        "wifi",
        "Wi-Fi",
        "Utilities",
        "Let guests join a Wi-Fi network without typing the password.",
        "free",
        QRCode.QRType.WIFI,
        (
            _field("ssid", "Network name", placeholder="Guest WiFi"),
            _field("password", "Password", required=False, placeholder="Network password"),
            _field("security", "Security", "select", choices=(("WPA", "WPA/WPA2"), ("WEP", "WEP"), ("nopass", "No password"))),
            _field("hidden", "Hidden network", "checkbox", required=False),
        ),
        "wifi",
    ),
    QRTemplate(
        "vcard-contact",
        "vCard/contact",
        "Personal",
        "Share contact details that save directly into a phone address book.",
        "free",
        QRCode.QRType.VCARD,
        (
            _field("full_name", "Full name", placeholder="Priya Sharma"),
            _field("phone", "Phone number", "tel", placeholder="+91 98765 43210"),
            _field("email", "Email", "email", required=False, placeholder="name@example.com"),
            _field("organization", "Company", required=False, placeholder="Narvyn"),
            _field("website", "Website", "url", required=False, placeholder="https://example.com"),
        ),
        "contact",
    ),
    QRTemplate(
        "pdf-document",
        "PDF/document",
        "Business",
        "Link scanners to a menu PDF, catalogue, brochure, manual, or policy document.",
        "pro",
        QRCode.QRType.DYNAMIC,
        (_field("document_url", "Document URL", "url", placeholder="https://example.com/catalogue.pdf"),),
        "document",
    ),
    QRTemplate(
        "app-download",
        "App download",
        "Marketing",
        "Send users to the App Store, Play Store, or a smart app download page.",
        "pro",
        QRCode.QRType.DYNAMIC,
        (_field("app_url", "App download URL", "url", placeholder="https://example.com/app"),),
        "app",
    ),
    QRTemplate(
        "google-review",
        "Google review",
        "Marketing",
        "Make it easy for customers to leave a public review after service.",
        "pro",
        QRCode.QRType.DYNAMIC,
        (_field("review_url", "Google review URL", "url", placeholder="https://g.page/r/.../review"),),
        "review",
    ),
    QRTemplate(
        "certificate-verification",
        "Certificate verification",
        "Education",
        "Let anyone verify a certificate, badge, warranty, or official document.",
        "free",
        QRCode.QRType.DYNAMIC,
        (
            _field("certificate_id", "Certificate ID", placeholder="CERT-2026-001"),
            _field("verification_url", "Verification URL", "url", placeholder="https://example.com/verify/CERT-2026-001"),
        ),
        "shield",
    ),
    QRTemplate(
        "whatsapp-chat",
        "WhatsApp chat",
        "Business",
        "Start a WhatsApp conversation with a prefilled enquiry message.",
        "free",
        QRCode.QRType.WHATSAPP,
        (
            _field("phone", "WhatsApp number", "tel", placeholder="+91 98765 43210"),
            _field("message", "Prefilled message", required=False, placeholder="Hi, I want to know more"),
        ),
        "chat",
    ),
    QRTemplate(
        "location-map",
        "Location/map",
        "Utilities",
        "Open a maps destination for stores, venues, offices, or event locations.",
        "free",
        QRCode.QRType.DYNAMIC,
        (
            _field("place_name", "Place name", required=False, placeholder="Narvyn Cafe"),
            _field("address", "Address or map URL", placeholder="221B Baker Street, London"),
        ),
        "map",
    ),
    QRTemplate(
        "email",
        "Email",
        "Personal",
        "Open a prefilled email to support, sales, or a personal contact.",
        "free",
        QRCode.QRType.EMAIL,
        (
            _field("email", "Email address", "email", placeholder="support@example.com"),
            _field("subject", "Subject", required=False, placeholder="Support request"),
            _field("body", "Message", "textarea", required=False, placeholder="Write a short message"),
        ),
        "email",
    ),
    QRTemplate(
        "sms",
        "SMS",
        "Personal",
        "Open a prefilled SMS for support, offers, bookings, or quick replies.",
        "free",
        QRCode.QRType.SMS,
        (
            _field("phone", "Phone number", "tel", placeholder="+91 98765 43210"),
            _field("message", "Message", required=False, placeholder="I am interested"),
        ),
        "sms",
    ),
    QRTemplate(
        "social-profile",
        "Social profile",
        "Marketing",
        "Share Instagram, LinkedIn, YouTube, X, Facebook, or any social profile.",
        "free",
        QRCode.QRType.DYNAMIC,
        (
            _field("platform", "Platform", required=False, placeholder="Instagram"),
            _field("profile_url", "Profile URL", "url", placeholder="https://instagram.com/brand"),
        ),
        "social",
    ),
)

TEMPLATE_BY_SLUG = {template.slug: template for template in TEMPLATES}


def user_can_use_template(user, template):
    return PLAN_ORDER.get(user.active_plan_code, 0) >= PLAN_ORDER.get(template.plan, 0)


def template_context(user):
    return [
        {
            "spec": template,
            "available": user_can_use_template(user, template),
        }
        for template in TEMPLATES
    ]


def get_template(slug):
    try:
        return TEMPLATE_BY_SLUG[slug]
    except KeyError as exc:
        raise ValidationError("Unknown QR template.") from exc


def build_template_qr_data(template, data):
    values = {field.name: (data.get(field.name, "") or "").strip() for field in template.fields}
    errors = {}
    for field in template.fields:
        if field.kind == "checkbox":
            continue
        if field.required and not values[field.name]:
            errors[field.name] = f"{field.label} is required."
    name = (data.get("name") or "").strip()
    if not name:
        errors["name"] = "QR name is required."
    if errors:
        raise ValidationError(errors)

    builder = _BUILDERS.get(template.slug)
    if not builder:
        raise ValidationError("Template is not configured.")
    payload = builder(values)
    payload.setdefault("destination_url", "")
    payload.setdefault("content", payload.get("destination_url", ""))
    payload.setdefault("qr_type", template.qr_type)
    payload["name"] = name
    campaign_name = (data.get("campaign_name") or "").strip() or f"{name} campaign"
    payload["campaign_name"] = campaign_name[:200]
    payload["tags"] = [template.category.lower(), template.slug]
    return payload


def _url(value, field_name="URL"):
    try:
        return normalize_http_url(value)
    except ValidationError as exc:
        raise ValidationError({field_name: "Enter a valid http or https URL."}) from exc


def _amount(value, *, required=False):
    if not value:
        if required:
            raise ValidationError({"amount": "Amount is required."})
        return ""
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValidationError({"amount": "Enter a valid amount."}) from exc
    if amount <= 0:
        raise ValidationError({"amount": "Amount must be greater than zero."})
    return f"{amount:.2f}"


def _validate_vpa(value):
    if not re.fullmatch(r"[A-Za-z0-9.\-_]{2,}@[A-Za-z][A-Za-z0-9.\-_]{2,}", value or ""):
        raise ValidationError({"payee_vpa": "Enter a valid UPI ID such as business@upi."})
    return value


def _upi(values, *, invoice=False):
    payee_vpa = _validate_vpa(values["payee_vpa"])
    payee_name = values["payee_name"]
    amount = _amount(values.get("amount", ""), required=invoice)
    note = values.get("note") or values.get("invoice_number") or "QR payment"
    params = {"pa": payee_vpa, "pn": payee_name, "cu": "INR"}
    if amount:
        params["am"] = amount
    if note:
        params["tn"] = note
    return {"qr_type": QRCode.QRType.TEXT, "content": f"upi://pay?{urlencode(params)}"}


def _dynamic(url, field_name="url"):
    normalized = _url(url, field_name)
    return {"qr_type": QRCode.QRType.DYNAMIC, "content": normalized, "destination_url": normalized}


def _website(values):
    return _dynamic(values["url"])


def _menu(values):
    return _dynamic(values["menu_url"], "menu_url")


def _invoice(values):
    return _upi(values, invoice=True)


def _event(values):
    return _dynamic(values["ticket_url"], "ticket_url")


def _attendance(values):
    return _dynamic(values["attendance_url"], "attendance_url")


def _product(values):
    return _dynamic(values["product_url"], "product_url")


def _feedback(values):
    return _dynamic(values["feedback_url"], "feedback_url")


def _coupon(values):
    return _dynamic(values["offer_url"], "offer_url")


def _wifi(values):
    ssid = _escape_wifi(values["ssid"])
    security = values.get("security") or "WPA"
    if security not in {"WPA", "WEP", "nopass"}:
        raise ValidationError({"security": "Choose a valid Wi-Fi security type."})
    password = _escape_wifi(values.get("password", ""))
    hidden = "true" if values.get("hidden") in {"on", "true", "1", True} else "false"
    content = f"WIFI:T:{security};S:{ssid};P:{password};H:{hidden};;"
    return {"qr_type": QRCode.QRType.WIFI, "content": content}


def _escape_wifi(value):
    return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace(":", "\\:").replace('"', '\\"')


def _vcard(values):
    validate_phone_number(values["phone"], "phone", "Enter a valid phone number with country code.")
    if values.get("email"):
        try:
            EMAIL_VALIDATOR(values["email"])
        except ValidationError as exc:
            raise ValidationError({"email": "Enter a valid email address."}) from exc
    website = _url(values["website"], "website") if values.get("website") else ""
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{values['full_name']}", f"N:{values['full_name']};;;;", f"TEL:{values['phone']}"]
    if values.get("email"):
        lines.append(f"EMAIL:{values['email']}")
    if values.get("organization"):
        lines.append(f"ORG:{values['organization']}")
    if website:
        lines.append(f"URL:{website}")
    lines.append("END:VCARD")
    return {"qr_type": QRCode.QRType.VCARD, "content": "\n".join(lines)}


def _document(values):
    return _dynamic(values["document_url"], "document_url")


def _app(values):
    return _dynamic(values["app_url"], "app_url")


def _review(values):
    return _dynamic(values["review_url"], "review_url")


def _certificate(values):
    return _dynamic(values["verification_url"], "verification_url")


def _whatsapp(values):
    validate_phone_number(values["phone"], "phone", "Enter a valid WhatsApp phone number with country code.")
    return {"qr_type": QRCode.QRType.WHATSAPP, "content": build_whatsapp_content(values["phone"], values.get("message", ""))}


def _location(values):
    address = values["address"]
    if address.lower().startswith(("http://", "https://")):
        return _dynamic(address, "address")
    query = " ".join(part for part in (values.get("place_name"), address) if part)
    return _dynamic(f"https://www.google.com/maps/search/?api=1&query={urlencode({'q': query})[2:]}", "address")


def _email(values):
    try:
        EMAIL_VALIDATOR(values["email"])
    except ValidationError as exc:
        raise ValidationError({"email": "Enter a valid email address."}) from exc
    return {"qr_type": QRCode.QRType.EMAIL, "content": build_email_content(values["email"], values.get("subject", ""), values.get("body", ""))}


def _sms(values):
    validate_phone_number(values["phone"], "phone", "Enter a valid SMS phone number with country code.")
    return {"qr_type": QRCode.QRType.SMS, "content": build_sms_content(values["phone"], values.get("message", ""))}


def _social(values):
    return _dynamic(values["profile_url"], "profile_url")


_BUILDERS = {
    "website-url": _website,
    "restaurant-menu": _menu,
    "upi-payment": _upi,
    "invoice-payment": _invoice,
    "event-ticket": _event,
    "attendance": _attendance,
    "product-page": _product,
    "feedback-form": _feedback,
    "coupon": _coupon,
    "wifi": _wifi,
    "vcard-contact": _vcard,
    "pdf-document": _document,
    "app-download": _app,
    "google-review": _review,
    "certificate-verification": _certificate,
    "whatsapp-chat": _whatsapp,
    "location-map": _location,
    "email": _email,
    "sms": _sms,
    "social-profile": _social,
}
