"""Membership checkout and payment callbacks."""
import logging
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.models import MembershipOrder
from apps.accounts.payments import (
    PaymentGatewayNotConfigured,
    amount_to_paise,
    amount_to_paytm_value,
    create_paytm_transaction,
    fetch_paytm_transaction_status,
    paytm_configured,
    paytm_js_url,
    paytm_status_success,
    verify_paytm_signature,
)
from apps.accounts.plans import BILLING_CYCLES, PAID_PLAN_CODES, get_plan, plan_period_end

logger = logging.getLogger(__name__)


def _activate_membership(user, plan_code, billing_cycle):
    plan = get_plan(plan_code)
    now = timezone.now()
    start = user.plan_expires_at if user.plan == plan_code and user.plan_expires_at and user.plan_expires_at > now else now
    expires_at = plan_period_end(billing_cycle, start=start)

    user.plan = plan.code
    user.role = plan.role
    user.plan_started_at = now
    user.plan_expires_at = expires_at
    user.save(update_fields=["plan", "role", "plan_started_at", "plan_expires_at"])
    return now, expires_at


def _mark_order_paid(order, *, payment_id, signature="", payload=None):
    if order.status == MembershipOrder.Status.PAID:
        return order

    with transaction.atomic():
        order = MembershipOrder.objects.select_for_update().select_related("user").get(pk=order.pk)
        if order.status == MembershipOrder.Status.PAID:
            return order
        started_at, expires_at = _activate_membership(order.user, order.plan_code, order.billing_cycle)
        order.status = MembershipOrder.Status.PAID
        order.provider_payment_id = payment_id or order.provider_payment_id
        order.provider_signature = signature or order.provider_signature
        order.membership_started_at = started_at
        order.membership_expires_at = expires_at
        order.paid_at = timezone.now()
        if payload:
            order.raw_payload = payload
        order.save(update_fields=[
            "status", "provider_payment_id", "provider_signature", "membership_started_at",
            "membership_expires_at", "paid_at", "raw_payload", "updated_at",
        ])
        return order


@login_required
def checkout(request, plan_code):
    plan = get_plan(plan_code)
    billing_cycle = request.GET.get("billing", "monthly")
    if plan.code not in PAID_PLAN_CODES or billing_cycle not in BILLING_CYCLES:
        messages.error(request, "Choose a valid paid membership plan.")
        return redirect("core:pricing")

    amount_paise = amount_to_paise(plan.price(billing_cycle))
    context = {
        "plan": plan,
        "billing_cycle": billing_cycle,
        "amount_paise": amount_paise,
        "amount_inr": plan.price(billing_cycle),
        "amount_paytm": amount_to_paytm_value(amount_paise),
        "gateway_configured": paytm_configured(),
        "paytm_mid": getattr(settings, "PAYTM_MID", ""),
    }

    if not paytm_configured():
        context["gateway_error"] = "Paytm is not configured yet. Add PAYTM_MID and PAYTM_MERCHANT_KEY to enable UPI checkout."
        return render(request, "billing/checkout.html", context, status=503)

    provider_order_id = f"M{uuid.uuid4().hex[:31]}"
    receipt = provider_order_id
    callback_url = request.build_absolute_uri(reverse("accounts:billing_callback"))
    try:
        remote_order = create_paytm_transaction(
            order_id=provider_order_id,
            amount_paise=amount_paise,
            callback_url=callback_url,
            user=request.user,
            notes={
                "user_id": str(request.user.id),
                "plan_code": plan.code,
                "billing_cycle": billing_cycle,
            },
        )
    except PaymentGatewayNotConfigured as exc:
        context["gateway_error"] = str(exc)
        return render(request, "billing/checkout.html", context, status=503)
    except Exception as exc:
        logger.exception("Failed to create Paytm transaction for %s: %s", request.user.id, exc)
        context["gateway_error"] = "Payment gateway is temporarily unavailable. Please try again."
        return render(request, "billing/checkout.html", context, status=502)

    order = MembershipOrder.objects.create(
        user=request.user,
        plan_code=plan.code,
        billing_cycle=billing_cycle,
        amount_paise=amount_paise,
        currency="INR",
        provider="paytm",
        provider_order_id=provider_order_id,
        receipt=receipt,
        raw_payload={"transaction": remote_order},
    )

    context.update({
        "order": order,
        "callback_url": callback_url,
        "paytm_js_url": paytm_js_url(),
        "paytm_txn_token": remote_order["body"]["txnToken"],
    })
    return render(request, "billing/checkout.html", context)


@csrf_exempt
@require_POST
def billing_callback(request):
    params = request.POST.dict()
    provider_order_id = params.get("ORDERID", "")
    payment_id = params.get("TXNID", "")
    signature = params.get("CHECKSUMHASH", "")
    order = get_object_or_404(MembershipOrder, provider="paytm", provider_order_id=provider_order_id)

    if not verify_paytm_signature(params, signature):
        order.status = MembershipOrder.Status.FAILED
        order.provider_payment_id = payment_id
        order.provider_signature = signature
        order.raw_payload = {"callback": dict(request.POST)}
        order.save(update_fields=["status", "provider_payment_id", "provider_signature", "raw_payload", "updated_at"])
        messages.error(request, "Payment verification failed. No membership changes were made.")
        return redirect("core:pricing")

    try:
        status_response = fetch_paytm_transaction_status(order.provider_order_id)
    except Exception as exc:
        logger.exception("Failed to verify Paytm transaction status for %s: %s", order.provider_order_id, exc)
        messages.error(request, "Payment status could not be verified yet. Please contact support if money was deducted.")
        return redirect("core:pricing")

    if not paytm_status_success(status_response):
        order.status = MembershipOrder.Status.FAILED
        order.provider_payment_id = payment_id
        order.provider_signature = signature
        order.raw_payload = {"callback": params, "status": status_response}
        order.save(update_fields=["status", "provider_payment_id", "provider_signature", "raw_payload", "updated_at"])
        messages.error(request, "Payment was not successful. No membership changes were made.")
        return redirect("core:pricing")

    payment_id = status_response.get("body", {}).get("txnId") or payment_id
    _mark_order_paid(order, payment_id=payment_id, signature=signature, payload={"callback": params, "status": status_response})
    messages.success(request, f"{get_plan(order.plan_code).name} membership is active.")
    return redirect("qrcodes:dashboard")


@csrf_exempt
@require_POST
def paytm_webhook(request):
    params = request.POST.dict()
    if not verify_paytm_signature(params):
        return HttpResponseBadRequest("Invalid signature.")

    provider_order_id = params.get("ORDERID", "")
    payment_id = params.get("TXNID", "")
    if params.get("STATUS") == "TXN_SUCCESS" and provider_order_id:
        order = MembershipOrder.objects.filter(provider="paytm", provider_order_id=provider_order_id).select_related("user").first()
        if order:
            status_response = fetch_paytm_transaction_status(order.provider_order_id)
            if paytm_status_success(status_response):
                _mark_order_paid(order, payment_id=payment_id, payload={"webhook": params, "status": status_response})

    return HttpResponse("ok")
