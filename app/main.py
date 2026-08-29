from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import get_db, close_db
from app.api.auth import router as auth_router
from app.api.jobs import router as jobs_router
from app.config import settings
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.data_dir, exist_ok=True)
    _check_config()
    await get_db()
    # planning/generating/stitching belonged to the process that just died;
    # nothing will push those jobs forward, so flag them as resumable.
    stale = await reconcile_interrupted()
    if stale:
        print(f"[startup] {stale} przerwanych jobów oznaczonych jako 'interrupted'")
    yield
    await close_db()


app = FastAPI(title="Video Generator POC", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(jobs_router)

# Serve static files (frontend).
# Keep this LAST — Mount("/") matches every path, so any router included below
# it is silently unreachable (its routes would return static-file 404s).
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
