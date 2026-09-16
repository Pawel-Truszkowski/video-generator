"""Stripe client over raw HTTP, no SDK.

Same call as for Resend (`app/mail/resend_sender.py`): the `stripe` package pulls
its own dependencies, and `pip install` is the most memory-hungry moment of a
deploy on a 1 GB box. Only two things are needed here — creating a Checkout
session and verifying a webhook signature — and both are shorter than SDK setup.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx

from app.config import settings

CHECKOUT_ENDPOINT = "https://api.stripe.com/v1/checkout/sessions"


class StripeError(RuntimeError):
    pass


class WebhookVerificationError(Exception):
    """The webhook signature does not match, is stale, or is malformed."""


def package_by_id(package_id: str) -> dict | None:
    for pkg in settings.credit_packages:
        if pkg["id"] == package_id:
            return pkg
    return None


async def create_checkout_session(package: dict, user_id: str, user_email: str) -> str:
    """Create a Checkout session and return its redirect URL. Raises StripeError.

    `client_reference_id` carries the user_id through the payment flow and comes
    back in the webhook — the only link between a payment and an account, since
    the webhook arrives from Stripe and carries no session cookie.

    `metadata[package_id]` tells the webhook which package was bought. The credit
    amount is deliberately not in the metadata: the webhook looks it up in our own
    price table, so the server decides how much lands on the account.
    """
    if not settings.stripe_enabled:
        raise StripeError("Stripe is disabled (STRIPE_ENABLED=false)")
    if not settings.stripe_secret_key:
        raise StripeError("STRIPE_SECRET_KEY is not set")

    base = settings.base_url.rstrip("/")
    # Form-encoded, not JSON: the Stripe API rejects a JSON body and expresses
    # nesting with brackets in the field name.
    form = {
        "mode": "payment",
        "client_reference_id": user_id,
        "customer_email": user_email,
        "metadata[package_id]": package["id"],
        "metadata[user_id]": user_id,
        "success_url": f"{base}/?credits=success",
        "cancel_url": f"{base}/?credits=cancel",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": str(package["price_usd"] * 100),
        "line_items[0][price_data][product_data][name]": (
            f"{package['label']} — {package['credits']} kredytów"
        ),
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            CHECKOUT_ENDPOINT,
            auth=(settings.stripe_secret_key, ""),
            data=form,
        )

    if res.status_code >= 300:
        raise StripeError(f"Stripe error {res.status_code}: {res.text[:500]}")

    url = res.json().get("url")
    if not url:
        raise StripeError("Stripe returned no session URL")
    return url


def verify_webhook(raw_body: bytes, signature_header: str) -> dict:
    """Verify a webhook signature and return the event. Raises on failure.

    The signature covers the raw BYTES of the body, not re-serialised JSON, which
    would change whitespace and key order and never match. The header looks like
    `t=<timestamp>,v1=<hex>,v1=<hex>` — several v1 signatures can appear while a
    secret is being rotated, so one match is enough.
    """
    if not settings.stripe_webhook_secret:
        raise WebhookVerificationError("STRIPE_WEBHOOK_SECRET is not set")
    if not signature_header:
        raise WebhookVerificationError("Missing Stripe-Signature header")

    timestamp = None
    signatures = []
    for part in signature_header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)

    if timestamp is None or not signatures:
        raise WebhookVerificationError("Malformed Stripe-Signature header")

    try:
        age = time.time() - int(timestamp)
    except ValueError:
        raise WebhookVerificationError("Invalid timestamp in signature") from None

    # A signature does not expire on its own, so without this an intercepted
    # request could be replayed forever.
    if abs(age) > settings.stripe_webhook_tolerance_s:
        raise WebhookVerificationError(f"Signature older than {settings.stripe_webhook_tolerance_s}s")

    expected = hmac.new(
        settings.stripe_webhook_secret.encode(),
        f"{timestamp}.".encode() + raw_body,
        hashlib.sha256,
    ).hexdigest()

    # compare_digest, not ==: constant-time comparison leaks nothing about how
    # many leading characters were guessed right.
    if not any(hmac.compare_digest(expected, sig) for sig in signatures):
        raise WebhookVerificationError("Signature mismatch")

    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        raise WebhookVerificationError("Webhook body is not valid JSON") from None
