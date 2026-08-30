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
from app.api.auth import router as auth_router
from app.api.jobs import is_job_running, router as jobs_router
from app.config import settings
from app.services import cleanup
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
# uploady + wersje _processed + klatki + klipy per scena + gotowy film.
MIN_FREE_DISK_MB = 500
# Musi zmiescic sie w `timeout: 10s` healthchecku z docker-compose.prod.yml.
DB_CHECK_TIMEOUT_S = 3.0


app = FastAPI(title="Video Generator POC", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(jobs_router)


@app.get("/health")
async def health():
    """Healthcheck dla dockera i monitoringu (docker-compose.prod.yml, co 60 s).

    Musi byc zarejestrowany PRZED mountem StaticFiles ponizej, inaczej
    Mount("/") przechwyci /health i zwroci 404 statycznego pliku.

    Uwaga na kontrakt: docker sam z siebie NIE restartuje kontenera oznaczonego
    jako unhealthy (robi to tylko swarm) - `docker compose ps` po prostu pokaze
    stan. Wiec falszywy alarm nie kladzie aplikacji.

    Sprawdzane sa dwie rzeczy, ktore moga byc zepsute przy zywym procesie
    uvicorna: baza i wolne miejsce w `data_dir`. Odpowiedz zawsze niesie oba
    pomiary - 503 mowi "cos nie gra", a cialo odpowiedzi mowi co, bez wchodzenia
    na serwer.
    """
    checks: dict[str, object] = {}
    healthy = True

    # Baza. Wlasny timeout, KROTSZY niz `timeout: 10s` w healthchecku compose:
    # SQLite w jednym procesie potrafi sie zablokowac pod obciazeniem ffmpeg,
    # a healthcheck ma wtedy dostac czytelne "db: timeout", nie zostac urwany
    # w polowie przez dockera.
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

    # Dysk. Retencja (app/services/cleanup.py) sprzata material roboczy starszy
    # niz RETENTION_DAYS, ale gotowe filmy w data/final nie maja retencji i rosna
    # w nieskonczonosc - a jeden job w trakcie zostawia jeszcze oryginaly, wersje
    # _processed, klatki i klipy. Prog jest twardy, bo ponizej niego generacja i
    # tak padnie - lepiej wiedziec przed, niz zbierac polowe klipow po.
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
    # 503, a nie 200 z polem "unhealthy": healthcheck w compose patrzy wylacznie
    # na to, czy urlopen() rzucil - kod odpowiedzi jest jedynym sygnalem, ktory
    # do dockera dociera. Cialo jest dla czlowieka.
    return JSONResponse(body, status_code=200 if healthy else 503)


# Serve static files (frontend).
# Keep this LAST — Mount("/") matches every path, so any router included below
# it is silently unreachable (its routes would return static-file 404s).
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
