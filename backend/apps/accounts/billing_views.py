"""Membership checkout and payment callbacks."""
import json
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
    active_payment_gateway,
    amount_to_paise,
    amount_to_cashfree_value,
    amount_to_paytm_value,
    cashfree_configured,
    cashfree_mode,
    cashfree_order_success,
    cashfree_success_payment_id,
    CASHFREE_JS_URL,
    create_cashfree_order,
    create_paytm_transaction,
    fetch_cashfree_order,
    fetch_cashfree_order_payments,
    fetch_paytm_transaction_status,
    paytm_configured,
    paytm_js_url,
    paytm_status_success,
    verify_cashfree_webhook_signature,
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


def _mark_order_failed(order, *, payment_id="", signature="", payload=None):
    if order.status == MembershipOrder.Status.PAID:
        return order
    order.status = MembershipOrder.Status.FAILED
    order.provider_payment_id = payment_id or order.provider_payment_id
    order.provider_signature = signature or order.provider_signature
    if payload:
        order.raw_payload = payload
    order.save(update_fields=["status", "provider_payment_id", "provider_signature", "raw_payload", "updated_at"])
    return order


def _cashfree_payment_id(order_id, order_response):
    try:
        return cashfree_success_payment_id(fetch_cashfree_order_payments(order_id)) or str(order_response.get("cf_order_id") or "")
    except Exception:
        logger.exception("Failed to fetch Cashfree payments for %s", order_id)
        return str(order_response.get("cf_order_id") or "")


def _finalize_cashfree_order(order, *, source, signature=""):
    order_response = fetch_cashfree_order(order.provider_order_id)
    if cashfree_order_success(order_response):
        payment_id = _cashfree_payment_id(order.provider_order_id, order_response)
        return _mark_order_paid(order, payment_id=payment_id, signature=signature, payload={source: order_response})
    if order_response.get("order_status") in {"EXPIRED", "TERMINATED"}:
        _mark_order_failed(order, payload={source: order_response})
    return order


@login_required
def checkout(request, plan_code):
    plan = get_plan(plan_code)
    billing_cycle = request.GET.get("billing", "monthly")
    if plan.code not in PAID_PLAN_CODES or billing_cycle not in BILLING_CYCLES:
        messages.error(request, "Choose a valid paid membership plan.")
        return redirect("core:pricing")

    selected_billing = plan.billing_option(billing_cycle)
    amount_paise = amount_to_paise(selected_billing.price_inr)
    gateway = active_payment_gateway()
    context = {
        "plan": plan,
        "billing_cycle": billing_cycle,
        "billing_options": plan.billing_options(),
        "selected_billing": selected_billing,
        "amount_paise": amount_paise,
        "amount_inr": selected_billing.price_inr,
        "amount_cashfree": amount_to_cashfree_value(amount_paise),
        "amount_paytm": amount_to_paytm_value(amount_paise),
        "gateway": gateway,
        "gateway_configured": cashfree_configured() if gateway == "cashfree" else paytm_configured(),
        "paytm_mid": getattr(settings, "PAYTM_MID", ""),
    }

    if gateway == "cashfree":
        return _cashfree_checkout(request, plan, billing_cycle, amount_paise, context)

    return _paytm_checkout(request, plan, billing_cycle, amount_paise, context)


def _paytm_checkout(request, plan, billing_cycle, amount_paise, context):
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


def _cashfree_checkout(request, plan, billing_cycle, amount_paise, context):
    if not cashfree_configured():
        context["gateway_error"] = "Cashfree is not configured yet. Add CASHFREE_CLIENT_ID and CASHFREE_CLIENT_SECRET to enable UPI checkout."
        return render(request, "billing/checkout.html", context, status=503)

    provider_order_id = f"CF{uuid.uuid4().hex[:32]}"
    receipt = provider_order_id
    return_url = request.build_absolute_uri(reverse("accounts:cashfree_callback")) + "?order_id={order_id}"
    notify_url = request.build_absolute_uri(reverse("accounts:cashfree_webhook"))
    try:
        remote_order = create_cashfree_order(
            order_id=provider_order_id,
            amount_paise=amount_paise,
            return_url=return_url,
            notify_url=notify_url,
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
        logger.exception("Failed to create Cashfree order for %s: %s", request.user.id, exc)
        context["gateway_error"] = "Payment gateway is temporarily unavailable. Please try again."
        return render(request, "billing/checkout.html", context, status=502)

    order = MembershipOrder.objects.create(
        user=request.user,
        plan_code=plan.code,
        billing_cycle=billing_cycle,
        amount_paise=amount_paise,
        currency="INR",
        provider="cashfree",
        provider_order_id=provider_order_id,
        receipt=receipt,
        raw_payload={"order": remote_order},
    )

    context.update({
        "order": order,
        "cashfree_js_url": CASHFREE_JS_URL,
        "cashfree_mode": cashfree_mode(),
        "cashfree_payment_session_id": remote_order["payment_session_id"],
    })
    return render(request, "billing/checkout.html", context)


@login_required
def cashfree_callback(request):
    provider_order_id = request.GET.get("order_id", "")
    order = get_object_or_404(
        MembershipOrder.objects.select_related("user"),
        provider="cashfree",
        provider_order_id=provider_order_id,
        user=request.user,
    )
    try:
        _finalize_cashfree_order(order, source="callback")
    except Exception as exc:
        logger.exception("Failed to verify Cashfree order %s: %s", provider_order_id, exc)
        messages.error(request, "Payment status could not be verified yet. Please contact support if money was deducted.")
        return redirect("core:pricing")

    order.refresh_from_db()
    if order.status == MembershipOrder.Status.PAID:
        messages.success(request, f"{get_plan(order.plan_code).name} membership is active.")
        return redirect("qrcodes:dashboard")

    messages.error(request, "Payment is not complete yet. No membership changes were made.")
    return redirect("core:pricing")


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


@csrf_exempt
@require_POST
def cashfree_webhook(request):
    signature = request.headers.get("x-webhook-signature", "")
    timestamp = request.headers.get("x-webhook-timestamp", "")
    raw_body = request.body

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponse("ok")

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    order_data = data.get("order") if isinstance(data.get("order"), dict) else {}
    payment_data = data.get("payment") if isinstance(data.get("payment"), dict) else {}
    provider_order_id = (
        order_data.get("order_id")
        or data.get("order_id")
        or payload.get("order_id")
    )

    if not provider_order_id and not order_data and not payment_data and not signature and not timestamp:
        return HttpResponse("ok")

    if not verify_cashfree_webhook_signature(raw_body=raw_body, signature=signature, timestamp=timestamp):
        if not provider_order_id or not MembershipOrder.objects.filter(
            provider="cashfree",
            provider_order_id=provider_order_id,
        ).exists():
            return HttpResponse("ok")
        return HttpResponseBadRequest("Invalid signature.")
    if not provider_order_id:
        return HttpResponseBadRequest("Missing order id.")

    order = MembershipOrder.objects.filter(
        provider="cashfree",
        provider_order_id=provider_order_id,
    ).select_related("user").first()
    if not order:
        return HttpResponse("ok")

    order_status = order_data.get("order_status") or data.get("order_status") or payload.get("order_status")
    payment_status = payment_data.get("payment_status") or data.get("payment_status") or payload.get("payment_status")
    if order_status == "PAID" or payment_status == "SUCCESS":
        try:
            _finalize_cashfree_order(order, source="webhook", signature=signature)
        except Exception:
            logger.exception("Failed to finalize Cashfree webhook for %s", provider_order_id)
            return HttpResponseBadRequest("Could not verify order.")
    elif order_status in {"EXPIRED", "TERMINATED"} or payment_status in {"FAILED", "CANCELLED"}:
        _mark_order_failed(order, payload={"webhook": payload})

    return HttpResponse("ok")
