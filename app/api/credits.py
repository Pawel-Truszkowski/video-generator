"""Balance, history and credit top-ups (Faza 3)."""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.auth.deps import require_user
from app.config import settings
from app.services import credits, stripe_client

# User routes: session required at router level, as in jobs.py, so a route added
# here later cannot be left public by omission.
router = APIRouter(prefix="/credits", tags=["credits"], dependencies=[Depends(require_user)])

# The webhook gets its OWN router, without require_user: Stripe sends no session
# cookie, so putting it on the router above would 401 every top-up. Its
# authentication is the HMAC signature, which is why this is a separate object
# rather than an exception carved out of a shared router.
webhook_router = APIRouter(prefix="/credits", tags=["credits"])


@router.get("")
async def get_credits(
    user: aiosqlite.Row = Depends(require_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return {
        "balance": await credits.balance(user["id"]),
        "history": await credits.history(user["id"], limit, offset),
    }


@router.get("/packages")
async def list_packages(user: aiosqlite.Row = Depends(require_user)):
    """Price list for the top-up screen.

    `stripe_enabled` ships with the packages so the UI can say purchasing is off
    instead of offering a button that ends in a 503.
    """
    return {
        "packages": list(settings.credit_packages),
        "stripe_enabled": settings.stripe_enabled,
        "balance": await credits.balance(user["id"]),
    }


class CheckoutRequest(BaseModel):
    package_id: str


@router.post("/checkout")
async def create_checkout(
    body: CheckoutRequest,
    user: aiosqlite.Row = Depends(require_user),
):
    if not settings.stripe_enabled:
        raise HTTPException(
            503, "Płatności są wyłączone na tej instancji — poproś administratora o kredyty"
        )

    package = stripe_client.package_by_id(body.package_id)
    if package is None:
        raise HTTPException(400, f"Nieznany pakiet: {body.package_id}")

    try:
        url = await stripe_client.create_checkout_session(package, user["id"], user["email"])
    except stripe_client.StripeError as e:
        raise HTTPException(502, f"Nie udało się rozpocząć płatności: {e}")

    return {"url": url}


@webhook_router.post("/webhook")
async def stripe_webhook(request: Request):
    """Top an account up after a paid Checkout session.

    Answers 200 for events we ignore and for a repeated top-up too: Stripe reads
    any other code as a failure and retries with growing backoff for days.

    400 is reserved for a bad signature, where retrying is the right response —
    the cause may be a secret rotation on our side.
    """
    # Raw bytes BEFORE any .json(): the signature covers what came over the wire,
    # and re-serialising changes whitespace.
    raw = await request.body()

    try:
        event = stripe_client.verify_webhook(raw, request.headers.get("stripe-signature", ""))
    except stripe_client.WebhookVerificationError as e:
        print(f"[stripe] rejected webhook: {e}")
        raise HTTPException(400, "Nieprawidłowy podpis")

    if event.get("type") != "checkout.session.completed":
        return {"received": True, "ignored": event.get("type")}

    session = event.get("data", {}).get("object", {})
    session_id = session.get("id")
    user_id = session.get("client_reference_id") or session.get("metadata", {}).get("user_id")
    package_id = session.get("metadata", {}).get("package_id")

    if not session_id or not user_id or not package_id:
        print(f"[stripe] incomplete session: {session_id=} {user_id=} {package_id=}")
        return {"received": True, "ignored": "incomplete"}

    # An unpaid session may still be paid later; crediting now would hand over
    # the goods before payment.
    if session.get("payment_status") not in (None, "paid", "no_payment_required"):
        return {"received": True, "ignored": session.get("payment_status")}

    # Credit amount from OUR price table by package id, never from the payload's
    # amount_total: the server decides how much lands on the account.
    package = stripe_client.package_by_id(package_id)
    if package is None:
        print(f"[stripe] unknown package in session {session_id}: {package_id}")
        return {"received": True, "ignored": "unknown package"}

    new_balance = await credits.grant(
        user_id,
        package["credits"],
        credits.PURCHASE,
        f"Zakup pakietu {package['label']} (${package['price_usd']})",
        ext_id=session_id,
    )

    if new_balance is None:
        # UNIQUE(ext_id) fired: Stripe redelivered this event.
        print(f"[stripe] session {session_id} already settled, skipping")
        return {"received": True, "duplicate": True}

    print(f"[stripe] +{package['credits']} credits for {user_id} (session {session_id})")
    return {"received": True, "balance": new_balance}
