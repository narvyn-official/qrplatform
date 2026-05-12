"""Membership plan catalogue and limit helpers."""
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    role: str
    monthly_price_inr: int
    yearly_price_inr: int
    max_qr: int
    max_scans: int
    features: tuple[str, ...]
    logo: bool = False
    custom_shapes: bool = False
    utm: bool = False
    export: bool = False
    api: bool = False
    scheduled: bool = False
    clone: bool = True

    def price(self, billing_cycle="monthly"):
        return self.yearly_price_inr if billing_cycle == "yearly" else self.monthly_price_inr


PLAN_CATALOG = {
    "free": Plan(
        code="free",
        name="Free",
        role="user",
        monthly_price_inr=0,
        yearly_price_inr=0,
        max_qr=5,
        max_scans=1_000,
        features=("5 active QR codes", "1,000 scans per QR", "Basic analytics", "PNG/SVG downloads"),
    ),
    "pro": Plan(
        code="pro",
        name="Pro",
        role="pro",
        monthly_price_inr=499,
        yearly_price_inr=4_999,
        max_qr=100,
        max_scans=50_000,
        features=(
            "100 active QR codes",
            "50,000 scans per QR",
            "UTM tracking",
            "Scheduled activation",
            "CSV/ZIP exports",
            "API keys",
        ),
        logo=True,
        custom_shapes=True,
        utm=True,
        export=True,
        api=True,
        scheduled=True,
    ),
    "enterprise": Plan(
        code="enterprise",
        name="Business",
        role="enterprise",
        monthly_price_inr=1_999,
        yearly_price_inr=19_999,
        max_qr=-1,
        max_scans=-1,
        features=(
            "Unlimited QR codes",
            "Unlimited tracked scans",
            "All Pro features",
            "Campaign routing",
            "Priority support",
            "Advanced API access",
        ),
        logo=True,
        custom_shapes=True,
        utm=True,
        export=True,
        api=True,
        scheduled=True,
    ),
}


PAID_PLAN_CODES = ("pro", "enterprise")
BILLING_CYCLES = ("monthly", "yearly")


def get_plan(code):
    return PLAN_CATALOG.get(code or "free", PLAN_CATALOG["free"])


def plan_limits(code):
    plan = get_plan(code)
    return {
        "max_qr": plan.max_qr,
        "max_scans": plan.max_scans,
        "logo": plan.logo,
        "custom_shapes": plan.custom_shapes,
        "utm": plan.utm,
        "export": plan.export,
        "api": plan.api,
        "scheduled": plan.scheduled,
        "clone": plan.clone,
    }


def plan_period_end(billing_cycle="monthly", start=None):
    start = start or timezone.now()
    if billing_cycle == "yearly":
        return start + timedelta(days=365)
    return start + timedelta(days=30)
