from __future__ import annotations

import re

import aiosqlite
from fastapi import Depends, HTTPException, Request, Response

from app.auth.tokens import sign_session, verify_session
from app.config import settings
from app.db import get_db

# Matches uuid4().hex[:12], the id format used for both jobs and users.
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")


# --- Cookies ------------------------------------------------------------------

def set_session_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=sign_session(user_id),
        max_age=settings.session_ttl_days * 86400,
        path="/",
        httponly=True,           # JS cannot read it, so XSS cannot exfiltrate it
        secure=settings.cookie_secure,  # env-driven: hard-coding True kills http://localhost
        samesite="lax",          # the magic link is a top-level cross-site nav; Strict drops it
    )


def clear_session_cookie(response: Response) -> None:
    # path/secure/samesite must match set_session_cookie, otherwise the browser
    # adds a second cookie instead of clearing the existing one.
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


# --- Dependencies -------------------------------------------------------------

async def get_current_user(request: Request) -> aiosqlite.Row | None:
    """Resolve the session cookie to a user row. None when absent/invalid. Never raises."""
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None

    user_id = verify_session(raw)
    if not user_id:
        return None

    # Reading the row rather than trusting the cookie payload costs one indexed
    # PK lookup and buys a kill-switch: `UPDATE users SET is_active=0` logs an
    # account out everywhere immediately.
    db = await get_db()
    cur = await db.execute(
        "SELECT id, email, created_at, is_active, is_admin FROM users WHERE id = ? AND is_active = 1",
        (user_id,),
    )
    return await cur.fetchone()


async def require_user(
    user: aiosqlite.Row | None = Depends(get_current_user),
) -> aiosqlite.Row:
    if user is None:
        raise HTTPException(status_code=401, detail="Nie jestes zalogowany")
    return user


async def require_admin(
    user: aiosqlite.Row = Depends(require_user),
) -> aiosqlite.Row:
    """Gate for the /admin router.

    is_admin comes from the row get_current_user re-reads on every request, so
    taking someone off ADMIN_EMAILS (plus a restart) cuts them off at once, the
    same kill-switch property as is_active.

    403, not the 404 of get_owned_job: a job id is worth hiding, the existence
    of /admin is not — app.js ships the button that calls it.
    """
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Brak uprawnień administratora")
    return user


async def get_owned_job(
    job_id: str,
    user: aiosqlite.Row = Depends(require_user),
) -> aiosqlite.Row:
    """The single ownership code path for every /jobs/{job_id} route.

    FastAPI fills `job_id` from the route's path parameter, so handlers just
    declare Depends(get_owned_job) and get the row.

    Returns 404 — not 403 — with an identical message for "does not exist" and
    "is not yours", so GET /jobs/<guess> cannot be used as a job-id oracle.
    """
    if not JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="Nie znaleziono zadania")

    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user["id"])
    )
    job = await cur.fetchone()
    if job is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono zadania")
    return job
