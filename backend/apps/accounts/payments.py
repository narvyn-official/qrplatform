"""Paytm payment helpers."""
import json
import logging
from decimal import Decimal

import paytmchecksum
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PAYTM_STAGING_HOST = "https://securegw-stage.paytm.in"
PAYTM_PRODUCTION_HOST = "https://securegw.paytm.in"


class PaymentGatewayNotConfigured(Exception):
    pass


def paytm_configured():
    return bool(getattr(settings, "PAYTM_MID", "") and getattr(settings, "PAYTM_MERCHANT_KEY", ""))


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
