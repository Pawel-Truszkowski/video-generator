from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.db import get_db, close_db
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.credits import router as credits_router, webhook_router as credits_webhook_router
from app.api.jobs import is_job_running, router as jobs_router
from app.auth.store import sync_admins
from app.config import settings
from app.services import cleanup, credits
from app.services.job_state import reconcile_interrupted


def _check_config() -> None:
    """Fail fast on unsafe production combinations.

    Settings is a frozen dataclass built at import time, so this is the earliest
    place that can raise without breaking `import app.config`. COOKIE_SECURE=true
    is the proxy for "deployed behind HTTPS".
    """
    if settings.cookie_secure and not settings.session_secret:
        raise RuntimeError("SESSION_SECRET must be set when COOKIE_SECURE=true")
    if settings.cookie_secure and settings.mail_provider == "console":
        raise RuntimeError(
            "MAIL_PROVIDER=console is a development backdoor (it returns the login "
            "link in the HTTP response); set MAIL_PROVIDER=resend"
        )
    if settings.mail_provider == "resend" and not settings.resend_api_key:
        raise RuntimeError("RESEND_API_KEY must be set when MAIL_PROVIDER=resend")

    if settings.mail_provider == "resend" and "@resend.dev" in settings.mail_from:
        raise RuntimeError(
            "MAIL_FROM still uses the shared @resend.dev sender, which only "
            "delivers to the Resend account owner; set a sender in a domain you "
            "control (docs/deployment.md §11)"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.data_dir, exist_ok=True)
    _check_config()
    await get_db()
    # After get_db(): _migrate() is what adds users.is_admin on an existing DB.
    await sync_admins(settings.admin_emails)
    # Accounts predating 0.7.0 never passed through a get_or_create_user that
    # granted credits. Idempotent (UNIQUE ext_id), so restarts do not stack.
    granted = await credits.backfill_welcome_credits()
    if granted:
        print(f"[startup] kredyty powitalne przyznane {granted} kontom")
    # planning/generating/stitching belonged to the process that just died;
    # nothing will push those jobs forward, so flag them as resumable.
    stale = await reconcile_interrupted()
    if stale:
        print(f"[startup] {len(stale)} przerwanych jobów oznaczonych jako 'interrupted'")
        # Their credit reservation died with the task, so run_generation's
        # `finally` never ran. settle_job is idempotent, so a job that did manage
        # to settle yields 0 here.
        for job_id in stale:
            refunded = await credits.settle_job(job_id)
            if refunded:
                print(f"[startup] job {job_id}: zwrot {refunded} kredytów")

    # After reconcile_interrupted(), never before: the sweep skips jobs in an
    # active status, and until that call runs the jobs left by the dead process
    # still wear one — it would skip exactly what it should be cleaning.
    cleanup_task = asyncio.create_task(cleanup.retention_loop(is_job_running))

    yield

    # Cancelled before close_db(): a loop woken during shutdown would otherwise
    # find the connection gone and log a failed sweep on every clean stop.
    cleanup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cleanup_task
    await close_db()


# Prog wolnego miejsca w data_dir. 500 MB to okolo jednego joba z zapasem:
# uploads + _processed versions + frames + per-scene clips + the finished film.
MIN_FREE_DISK_MB = 500
# Must fit inside the `timeout: 10s` healthcheck in docker-compose.prod.yml.
DB_CHECK_TIMEOUT_S = 3.0


app = FastAPI(title="Video Generator POC", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(jobs_router)
app.include_router(admin_router)
app.include_router(credits_router)
# A separate router because the Stripe webhook cannot require a session -- see
# api/credits.py.
app.include_router(credits_webhook_router)


@app.get("/health")
async def health():
    """Healthcheck for docker and monitoring (docker-compose.prod.yml, every 60s).

    Must be registered BEFORE the StaticFiles mount below, or Mount("/") swallows
    /health and answers with a static-file 404.

    Note the contract: docker does NOT restart a container marked unhealthy on its
    own (only swarm does) — `docker compose ps` merely shows the state. So a false
    alarm does not take the app down.

    Two things are checked, both of which can be broken while the uvicorn process
    is alive: the database and free space in `data_dir`. The response always
    carries both measurements — a 503 says "something is wrong" and the body says
    what, without logging into the server.
    """
    checks: dict[str, object] = {}
    healthy = True

    # The DB, with its own timeout, SHORTER than compose's `timeout: 10s`:
    # single-process SQLite can block under ffmpeg load, and the healthcheck
    # should then read "db: timeout" rather than be cut off by docker.
    try:
        db = await get_db()
        cur = await asyncio.wait_for(db.execute("SELECT 1"), timeout=DB_CHECK_TIMEOUT_S)
        try:
            await cur.fetchone()
        finally:
            await cur.close()
        checks["db"] = "ok"
    except asyncio.TimeoutError:
        checks["db"] = f"timeout po {DB_CHECK_TIMEOUT_S}s"
        healthy = False
    except Exception as exc:  # plik bazy zniknal, wolumin niezamontowany, I/O error
        checks["db"] = f"{type(exc).__name__}: {exc}"
        healthy = False

    # Disk. Retention sweeps working material older than RETENTION_DAYS, but
    # finished films in data/final are never swept and grow without bound — and a
    # job in flight adds originals, _processed versions, frames and clips on top.
    # The threshold is hard because below it generation fails anyway.
    try:
        usage = shutil.disk_usage(settings.data_dir)
        free_mb = usage.free // (1024 * 1024)
        checks["disk_free_mb"] = free_mb
        if free_mb < MIN_FREE_DISK_MB:
            checks["disk"] = f"ponizej progu {MIN_FREE_DISK_MB} MB"
            healthy = False
        else:
            checks["disk"] = "ok"
    except OSError as exc:
        checks["disk"] = f"{type(exc).__name__}: {exc}"
        healthy = False

    body = {"status": "ok" if healthy else "unhealthy", "checks": checks}
    # 503, not 200 with an "unhealthy" field: compose's healthcheck only looks at
    # whether urlopen() raised, so the status code is the only signal that reaches
    # docker. The body is for a human.
    return JSONResponse(body, status_code=200 if healthy else 503)


# Keep this LAST — Mount("/") matches every path, so any router included below
# it is silently unreachable (its routes would return static-file 404s).
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
