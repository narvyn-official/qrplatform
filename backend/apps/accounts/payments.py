"""Payment gateway helpers."""
import base64
import hmac
import hashlib
import json
import logging
from decimal import Decimal

import paytmchecksum
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PAYTM_STAGING_HOST = "https://securegw-stage.paytm.in"
PAYTM_PRODUCTION_HOST = "https://securegw.paytm.in"
CASHFREE_SANDBOX_HOST = "https://sandbox.cashfree.com/pg"
CASHFREE_PRODUCTION_HOST = "https://api.cashfree.com/pg"
CASHFREE_JS_URL = "https://sdk.cashfree.com/js/v3/cashfree.js"


class PaymentGatewayNotConfigured(Exception):
    pass


def paytm_configured():
    return bool(getattr(settings, "PAYTM_MID", "") and getattr(settings, "PAYTM_MERCHANT_KEY", ""))


def active_payment_gateway():
    gateway = getattr(settings, "PAYMENT_GATEWAY", "paytm").strip().lower()
    return gateway if gateway in {"paytm", "cashfree"} else "paytm"


def cashfree_configured():
    return bool(
        getattr(settings, "CASHFREE_CLIENT_ID", "")
        and getattr(settings, "CASHFREE_CLIENT_SECRET", "")
    )


def amount_to_paise(amount_inr):
    return int(Decimal(amount_inr) * 100)


def amount_to_paytm_value(amount_paise):
    return f"{Decimal(amount_paise) / Decimal(100):.2f}"


def paytm_host():
    return PAYTM_PRODUCTION_HOST if getattr(settings, "PAYTM_ENVIRONMENT", "staging") == "production" else PAYTM_STAGING_HOST


def paytm_js_url():
    mid = getattr(settings, "PAYTM_MID", "")
    return f"{paytm_host()}/merchantpgpui/checkoutjs/merchants/{mid}.js"


def _json_for_signature(payload):
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _signature_for_body(body):
    return paytmchecksum.generateSignature(_json_for_signature(body), settings.PAYTM_MERCHANT_KEY)


def _post_signed_paytm_request(url, body):
    signature = _signature_for_body(body)
    payload = {"body": body, "head": {"signature": signature}}
    response = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        data=_json_for_signature(payload),
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def create_paytm_transaction(*, order_id, amount_paise, callback_url, user, notes=None):
    if not paytm_configured():
        raise PaymentGatewayNotConfigured("Paytm MID/merchant key are not configured.")

    body = {
        "requestType": "Payment",
        "mid": settings.PAYTM_MID,
        "websiteName": getattr(settings, "PAYTM_WEBSITE_NAME", "WEBSTAGING"),
        "orderId": order_id,
        "callbackUrl": callback_url,
        "txnAmount": {
            "value": amount_to_paytm_value(amount_paise),
            "currency": "INR",
        },
        "userInfo": {
            "custId": f"user_{user.id}",
            "email": user.email,
        },
    }
    if notes:
        body["extendInfo"] = {key: str(value) for key, value in notes.items()}

    url = f"{paytm_host()}/theia/api/v1/initiateTransaction?mid={settings.PAYTM_MID}&orderId={order_id}"
    response = _post_signed_paytm_request(url, body)
    result = response.get("body", {}).get("resultInfo", {})
    if result.get("resultStatus") != "S":
        message = result.get("resultMsg") or "Paytm did not create a transaction token."
        raise RuntimeError(message)
    if not response.get("body", {}).get("txnToken"):
        raise RuntimeError("Paytm response did not include a transaction token.")
    return response


def verify_paytm_signature(params, checksum=None):
    if not paytm_configured():
        return False
    values = {key: str(value) for key, value in params.items() if key != "CHECKSUMHASH"}
    checksum = checksum or params.get("CHECKSUMHASH", "")
    if not checksum:
        return False
    try:
        return bool(paytmchecksum.verifySignature(values, settings.PAYTM_MERCHANT_KEY, checksum))
    except Exception:
        logger.info("Paytm checksum verification failed.")
        return False


def fetch_paytm_transaction_status(order_id):
    if not paytm_configured():
        raise PaymentGatewayNotConfigured("Paytm MID/merchant key are not configured.")
    body = {"mid": settings.PAYTM_MID, "orderId": order_id}
    return _post_signed_paytm_request(f"{paytm_host()}/v3/order/status", body)


def paytm_status_success(status_response):
    body = status_response.get("body", {})
    return body.get("resultInfo", {}).get("resultStatus") == "TXN_SUCCESS"


def cashfree_host():
    if getattr(settings, "CASHFREE_ENVIRONMENT", "sandbox") == "production":
        return CASHFREE_PRODUCTION_HOST
    return CASHFREE_SANDBOX_HOST


def cashfree_mode():
    return "production" if getattr(settings, "CASHFREE_ENVIRONMENT", "sandbox") == "production" else "sandbox"


def _cashfree_headers(*, idempotency_key=None):
    if not cashfree_configured():
        raise PaymentGatewayNotConfigured("Cashfree Client ID/Secret are not configured.")
    headers = {
        "Content-Type": "application/json",
        "x-api-version": getattr(settings, "CASHFREE_API_VERSION", "2025-01-01"),
        "x-client-id": settings.CASHFREE_CLIENT_ID,
        "x-client-secret": settings.CASHFREE_CLIENT_SECRET,
    }
    if idempotency_key:
        headers["x-idempotency-key"] = str(idempotency_key)
    return headers


def amount_to_cashfree_value(amount_paise):
    return float(Decimal(amount_paise) / Decimal(100))


def _request_cashfree(method, path, *, payload=None, idempotency_key=None):
    response = requests.request(
        method,
        f"{cashfree_host()}{path}",
        headers=_cashfree_headers(idempotency_key=idempotency_key),
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def create_cashfree_order(*, order_id, amount_paise, return_url, notify_url, user, notes=None):
    if not cashfree_configured():
        raise PaymentGatewayNotConfigured("Cashfree Client ID/Secret are not configured.")

    payload = {
        "order_id": order_id,
        "order_amount": amount_to_cashfree_value(amount_paise),
        "order_currency": "INR",
        "customer_details": {
            "customer_id": f"user_{user.id}",
            "customer_email": user.email,
            "customer_name": getattr(user, "display_name", "") or user.email.split("@")[0],
            "customer_phone": getattr(settings, "CASHFREE_DEFAULT_CUSTOMER_PHONE", "9999999999"),
        },
        "order_meta": {
            "return_url": return_url,
            "notify_url": notify_url,
            "payment_methods": getattr(settings, "CASHFREE_PAYMENT_METHODS", "upi"),
        },
        "order_note": "Narvyn QR membership",
    }
    if notes:
        payload["order_tags"] = {key: str(value)[:255] for key, value in notes.items()}

    response = _request_cashfree("POST", "/orders", payload=payload, idempotency_key=order_id)
    if not response.get("payment_session_id"):
        raise RuntimeError("Cashfree response did not include a payment session id.")
    return response


def fetch_cashfree_order(order_id):
    if not order_id:
        raise ValueError("Cashfree order id is required.")
    return _request_cashfree("GET", f"/orders/{order_id}")


def fetch_cashfree_order_payments(order_id):
    if not order_id:
        raise ValueError("Cashfree order id is required.")
    return _request_cashfree("GET", f"/orders/{order_id}/payments")


def cashfree_order_success(order_response):
    return order_response.get("order_status") == "PAID"


def cashfree_success_payment_id(payments_response):
    if not isinstance(payments_response, list):
        return ""
    for payment in payments_response:
        if payment.get("payment_status") == "SUCCESS":
            return str(payment.get("cf_payment_id") or payment.get("payment_id") or "")
    return ""


def verify_cashfree_webhook_signature(*, raw_body, signature, timestamp):
    if not cashfree_configured() or not raw_body or not signature or not timestamp:
        return False
    if isinstance(raw_body, str):
        raw_body = raw_body.encode("utf-8")
    message = timestamp.encode("utf-8") + raw_body
    digest = hmac.new(
        settings.CASHFREE_CLIENT_SECRET.encode("utf-8"),
        message,
        hashlib.sha256,
    ).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, signature)
