from __future__ import annotations

import re
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator

from app.auth import store
# NOTE: `app.auth` is the logic package, this module is the router. Absolute
# imports are the Python 3 default, so this resolves to the package, not to self.
from app.auth.deps import clear_session_cookie, require_user, set_session_cookie
from app.auth.tokens import new_magic_token
from app.config import settings
from app.mail import get_sender

router = APIRouter(prefix="/auth", tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


# --- Rate limiting ------------------------------------------------------------
# Deliberately dependency-free and in-process, in the same spirit as _job_states.
# Limitations: resets on restart, and request.client.host becomes the proxy's IP
# once Caddy is in front (Faza 4 needs --proxy-headers or every user shares one
# bucket). Redis is the Faza 6 answer.
_login_hits: dict[str, list[float]] = {}


def _check_rate(key: str, limit: int) -> None:
    now = time.monotonic()  # monotonic, so NTP/clock jumps cannot widen the window
    window = settings.login_rate_window_s

    hits = [t for t in _login_hits.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, "Za duzo prob logowania. Odczekaj kilka minut.")

    hits.append(now)
    _login_hits[key] = hits

    if len(_login_hits) > 1000:
        for k in [k for k, v in _login_hits.items() if not v or now - v[-1] >= window]:
            _login_hits.pop(k, None)


class LoginRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _normalize(cls, v: str) -> str:
        v = v.strip().lower()
        if len(v) > 254 or not EMAIL_RE.fullmatch(v):
            raise ValueError("Nieprawidlowy adres email")
        return v


@router.post("/request-login")
async def request_login(request: Request, body: LoginRequest) -> dict:
    # Rate-limit before any DB write or mail send.
    _check_rate(
        f"ip:{request.client.host if request.client else 'unknown'}",
        settings.login_rate_per_ip,
    )
    _check_rate(f"email:{body.email}", settings.login_rate_per_email)

    user = await store.get_or_create_user(body.email)

    raw_token = new_magic_token()
    await store.create_magic_token(user["id"], raw_token)

    # settings.base_url first: request.base_url derives from the Host header,
    # which a caller can spoof to rewrite the link inside a real email.
    base = (settings.base_url or str(request.base_url)).rstrip("/")
    login_url = f"{base}/auth/callback?token={raw_token}"

    try:
        await get_sender().send_login_link(body.email, login_url)
    except Exception as e:
        print(f"Mail send failed for {body.email}: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(502, "Nie udalo sie wyslac emaila. Sprobuj ponownie.")

    result: dict = {"ok": True}
    # Full impersonation backdoor -> double-gated so a prod-flagged deploy cannot
    # expose it even if MAIL_PROVIDER is misconfigured.
    if settings.mail_provider == "console" and not settings.cookie_secure:
        result["login_url"] = login_url
    return result


@router.get("/callback")
async def auth_callback(token: str = Query(...)) -> RedirectResponse:
    """Must be a real route: StaticFiles(html=True) does directory-index
    resolution, not SPA fallback, so this path would otherwise 404."""
    user_id = await store.consume_magic_token(token)

    if user_id is None:
        return RedirectResponse("/?auth_error=invalid", status_code=303)

    await store.touch_last_login(user_id)

    # Browsers persist Set-Cookie from a 303 before following the redirect.
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, user_id)
    return response


@router.get("/me")
async def me(user: aiosqlite.Row = Depends(require_user)) -> dict:
    return {"email": user["email"], "created_at": user["created_at"]}


@router.post("/logout")
async def logout() -> Response:
    # No require_user: logout must be idempotent so a stale or garbage cookie
    # can always be cleared.
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response
