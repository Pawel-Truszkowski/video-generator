from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.jobs import remove_job
from app.auth.deps import JOB_ID_RE, require_admin
from app.db import get_db

# Router-level dependency, as in jobs.py: a route added here later cannot be
# left open by forgetting it. Handlers that need the admin's own row declare
# Depends(require_admin) again — FastAPI resolves it once per request.
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/jobs")
async def admin_list_jobs(
    status: str | None = Query(None, max_length=20),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Every user's jobs, newest first; `status` narrows to one (e.g. `error`).

    LEFT JOIN users, not JOIN: jobs from before 0.2.0 have user_id NULL. No user
    sees them in "Moje filmy", so this is the only place they can be found.
    """
    db = await get_db()
    rows = await db.execute(
        "SELECT j.id, j.status, j.prompt, j.model, j.est_cost_usd, j.created_at, j.error, "
        "       j.workdirs_purged_at, u.email AS user_email, "
        "       COUNT(s.id) AS scene_count, "
        "       COALESCE(SUM(s.duration_s), 0) AS total_duration_s "
        "  FROM jobs j "
        "  LEFT JOIN users u ON u.id = j.user_id "
        "  LEFT JOIN scenes s ON s.job_id = j.id "
        " WHERE (? IS NULL OR j.status = ?) "
        " GROUP BY j.id "
        " ORDER BY j.created_at DESC, j.id DESC "
        " LIMIT ? OFFSET ?",
        (status, status, limit, offset),
    )
    return {"jobs": [dict(r) for r in await rows.fetchall()]}


@router.delete("/jobs/{job_id}")
async def admin_delete_job(job_id: str, admin: aiosqlite.Row = Depends(require_admin)):
    """Delete any user's job. Same 409-while-running rule as DELETE /jobs/{id}."""
    if not JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(404, "Nie znaleziono zadania")

    db = await get_db()
    cur = await db.execute("SELECT status, user_id FROM jobs WHERE id = ?", (job_id,))
    job = await cur.fetchone()
    if job is None:
        raise HTTPException(404, "Nie znaleziono zadania")

    removed = await remove_job(job_id, job["status"])
    # The only record of who deleted someone else's film: there is no audit table.
    print(f"[admin] {admin['email']} usunął job {job_id} (właściciel: {job['user_id']})")
    return {"deleted": job_id, "removed_paths": removed}


@router.get("/users")
async def admin_list_users():
    """All accounts with their job count and summed cost.

    The cost is the planner's `est_cost_usd`, not what fal.ai actually billed —
    nothing records real spend until Faza 3. No pagination: a PoC with a
    handful of accounts.
    """
    db = await get_db()
    rows = await db.execute(
        "SELECT u.id, u.email, u.created_at, u.last_login_at, u.is_active, u.is_admin, "
        "       COUNT(j.id) AS job_count, "
        "       COALESCE(SUM(j.est_cost_usd), 0) AS est_cost_usd "
        "  FROM users u "
        "  LEFT JOIN jobs j ON j.user_id = u.id "
        " GROUP BY u.id "
        " ORDER BY u.created_at DESC"
    )
    return {"users": [dict(r) for r in await rows.fetchall()]}


class ActiveUpdate(BaseModel):
    is_active: bool


@router.post("/users/{user_id}/active")
async def admin_set_active(
    user_id: str,
    body: ActiveUpdate,
    admin: aiosqlite.Row = Depends(require_admin),
):
    """Block or unblock an account.

    Takes effect on that user's next request: get_current_user re-reads
    is_active every time, and consume_magic_token will not revive a blocked
    account from an old link. Jobs already running are left to finish —
    blocking stops new spending, it does not throw away a half-paid generation.
    """
    if user_id == admin["id"]:
        # Nothing in the UI could undo it: a blocked admin cannot log in to unblock.
        raise HTTPException(409, "Nie możesz zablokować własnego konta")

    db = await get_db()
    cur = await db.execute(
        "UPDATE users SET is_active = ? WHERE id = ?", (int(body.is_active), user_id)
    )
    await db.commit()
    if cur.rowcount != 1:
        raise HTTPException(404, "Nie znaleziono użytkownika")

    state = "odblokował" if body.is_active else "zablokował"
    print(f"[admin] {admin['email']} {state} konto {user_id}")
    return {"id": user_id, "is_active": body.is_active}
