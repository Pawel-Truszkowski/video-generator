from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from app.config import settings

# A *constant* dev fallback, not secrets.token_hex(): a per-process secret would
# invalidate every session on each container restart, which reads as "randomly
# logged out" rather than as a misconfiguration.
_DEV_SECRET = b"dev-only-insecure-session-secret-change-me"

if settings.session_secret:
    _SECRET = settings.session_secret.encode()
else:
    _SECRET = _DEV_SECRET
    print(
        "WARNING: SESSION_SECRET is not set - using an insecure development secret. "
        "Set SESSION_SECRET in .env before deploying.",
        flush=True,
    )

_VERSION = "v1"


# --- Magic-link token ---------------------------------------------------------

def new_magic_token() -> str:
    """256 bits of CSPRNG output. Sent in the email, never stored."""
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """Hash for storage at rest.

    Plain unsalted SHA-256 is correct here: the input is 256 bits of CSPRNG
    output, not a password, so there is no brute-force surface to stretch against.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


# --- Session cookie -----------------------------------------------------------

def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(signing_input: str) -> str:
    mac = hmac.new(_SECRET, signing_input.encode(), hashlib.sha256).digest()
    return _b64u_encode(mac)


def sign_session(user_id: str, ttl_days: int | None = None) -> str:
    """Build `v1.<payload>.<mac>` where payload is `user_id:expiry_epoch`."""
    ttl = settings.session_ttl_days if ttl_days is None else ttl_days
    exp = int(time.time()) + ttl * 86400
    payload = _b64u_encode(f"{user_id}:{exp}".encode())
    signing_input = f"{_VERSION}.{payload}"
    return f"{signing_input}.{_sign(signing_input)}"


def verify_session(value: str) -> str | None:
    """Return the user_id, or None if the cookie is absent/malformed/forged/expired.

    Never raises, and never distinguishes between failure modes.

    Expiry is checked from the *signed payload* rather than relying on the
    cookie's Max-Age, since a client can replay a cookie past its Max-Age.
    """
    if not value:
        return None
    try:
        version, payload, mac = value.split(".")
    except ValueError:
        return None
    if version != _VERSION:
        return None

    # The version prefix is part of the signed input, so it cannot be downgraded.
    if not hmac.compare_digest(_sign(f"{version}.{payload}"), mac):
        return None

    try:
        user_id, exp_raw = _b64u_decode(payload).decode().rsplit(":", 1)
        exp = int(exp_raw)
    except (ValueError, UnicodeDecodeError):
        return None

    if exp <= int(time.time()):
        return None
    return user_id or None
