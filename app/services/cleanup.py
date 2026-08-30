"""Retencja plikow i kasowanie jobow (roadmap Faza 1.3).

Jedyne miejsce w aplikacji, ktore usuwa pliki joba. Sciezki bierze wylacznie
z `job_state` -- gdyby budowalo wlasne, rozjechalyby sie z tym, co pipeline
naprawde zapisal, a skutkiem byloby ciche niesprzatanie polowy danych.

`data/final` jest wylaczony z retencji i kasuje go tylko purge_all() (czyli
DELETE /jobs/{id}): gotowy film jest produktem, za ktory ktos zaplacil.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from typing import Callable

from app.config import settings
from app.db import get_db
from app.services.job_state import (
    STALE_STATUSES,
    clips_dir,
    final_path,
    frames_dir,
    stitch_dir,
    uploads_dir,
)

# Katalogi trzymajace jeden podkatalog na job_id. `final` nie jest na liscie:
# tam kazdy plik ma wlasciciela i nie wolno go skasowac po samym wieku.
WORK_SUBDIRS = ("uploads", "frames", "clips", "stitch")


def _rmtree(path: str) -> bool:
    """Skasuj katalog. True, jesli cokolwiek zniknelo.

    Blad I/O nie moze przerwac calego sweepu: jeden zablokowany katalog
    zostawilby nieposprzatana cala reszte, a nastepna proba jest za dobe.
    """
    if not os.path.isdir(path):
        return False
    try:
        shutil.rmtree(path)
        return True
    except OSError as exc:
        print(f"[cleanup] nie udalo sie skasowac {path}: {type(exc).__name__}: {exc}")
        return False


def _remove_file(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        print(f"[cleanup] nie udalo sie skasowac {path}: {type(exc).__name__}: {exc}")
        return False


def purge_workdirs(job_id: str) -> int:
    """Skasuj material roboczy joba. Zwraca liczbe usunietych katalogow."""
    removed = 0
    for path in (uploads_dir(job_id), frames_dir(job_id), clips_dir(job_id),
                 stitch_dir(job_id)):
        removed += _rmtree(path)
    return removed


def purge_all(job_id: str) -> int:
    """purge_workdirs() + gotowy film."""
    return purge_workdirs(job_id) + _remove_file(final_path(job_id))


# --- Sweep --------------------------------------------------------------------

def _never_running(_job_id: str) -> bool:
    return False


async def _purge_old_jobs(is_running: Callable[[str], bool]) -> int:
    """Retencja jobow znanych bazie. Zwraca liczbe posprzatanych jobow."""
    db = await get_db()
    placeholders = ", ".join("?" * len(STALE_STATUSES))
    cur = await db.execute(
        # Dwa filtry statusu, bo lataja rozne dziury. Kolumna `status` chroni po
        # restarcie (job zostal 'generating' po martwym procesie -- ale to juz
        # zdazyl przestawic reconcile_interrupted), `is_running` chroni w trakcie
        # normalnej pracy, gdzie zywy task ma status aktywny i pisze do clips/.
        # Skasowanie plikow spod dzialajacego taska dalo by polowe filmu bez bledu.
        f"SELECT id FROM jobs "
        f" WHERE workdirs_purged_at IS NULL "
        f"   AND created_at < strftime('%Y-%m-%dT%H:%M:%SZ','now', ?) "
        f"   AND status NOT IN ({placeholders})",
        (f"-{settings.retention_days} days", *STALE_STATUSES),
    )
    job_ids = [row["id"] for row in await cur.fetchall()]

    purged = 0
    for job_id in job_ids:
        if is_running(job_id):
            continue
        purge_workdirs(job_id)
        # Znacznik leci nawet wtedy, gdy nie bylo czego kasowac: stan "material
        # roboczy tego joba juz nie istnieje" jest prawdziwy tak samo, a bez
        # zapisu sweep braleby ten sam job pod uwage co dobe, w nieskonczonosc.
        await db.execute(
            "UPDATE jobs SET workdirs_purged_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
            " WHERE id = ?",
            (job_id,),
        )
        purged += 1

    if purged:
        await db.commit()
    return purged


def _older_than(path: str, cutoff: float) -> bool:
    """False takze wtedy, gdy nie da sie odczytac mtime -- nie kasuj po omacku."""
    try:
        return os.path.getmtime(path) < cutoff
    except OSError:
        return False


async def _purge_orphans() -> int:
    """Skasuj material roboczy, do ktorego nie przyznaje sie zaden job.

    Luzny plik na szczycie tych katalogow jest sierota z definicji: obecny kod
    zawsze pisze do podkatalogu per job_id (stad relikty sprzed 0.3.0 nazwane
    uuid4().mp4 i niedokonczone `*.part`).

    Prog wieku jest tu konieczny, a nie ostrozny: `create_job` tworzy
    uploads/{job_id} PRZED INSERT-em, wiec job w trakcie wgrywania zdjec przez
    chwile wyglada dokladnie jak sierota.
    """
    db = await get_db()
    cur = await db.execute("SELECT id FROM jobs")
    known = {row["id"] for row in await cur.fetchall()}

    cutoff = time.time() - settings.retention_days * 86400
    removed = 0

    for sub in WORK_SUBDIRS:
        root = os.path.join(settings.data_dir, sub)
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            path = os.path.join(root, name)

            if os.path.isdir(path):
                if name in known or not _older_than(path, cutoff):
                    continue
                if _rmtree(path):
                    removed += 1
                    print(f"[cleanup] osierocony katalog usuniety: {sub}/{name}")
            elif _older_than(path, cutoff):
                if _remove_file(path):
                    removed += 1
                    print(f"[cleanup] osierocony plik usuniety: {sub}/{name}")

    return removed


async def sweep(is_running: Callable[[str], bool] = _never_running) -> dict[str, int]:
    """Jeden przebieg retencji. Zwraca liczniki do logu."""
    jobs = await _purge_old_jobs(is_running)
    orphans = await _purge_orphans()
    return {"jobs": jobs, "orphans": orphans}


async def retention_loop(is_running: Callable[[str], bool] = _never_running) -> None:
    """Sprzataj przy starcie, potem co `settings.cleanup_interval_h` godzin.

    Pierwszy przebieg od razu, bez czekania -- dzieki temu restart kontenera jest
    pelnoprawnym sposobem wymuszenia sprzatania, takze przy testowaniu.
    """
    interval_s = settings.cleanup_interval_h * 3600
    while True:
        try:
            result = await sweep(is_running)
            if result["jobs"] or result["orphans"]:
                print(
                    f"[cleanup] posprzatano {result['jobs']} jobow, "
                    f"{result['orphans']} osieroconych wpisow"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Petla musi przezyc bledny przebieg. Wyjatek tutaj konczylby task po
            # cichu i czyszczenie przestaloby dzialac az do nastepnego restartu,
            # bez zadnego sygnalu poza rosnacym dyskiem.
            print(f"[cleanup] sweep nieudany: {type(exc).__name__}: {exc}")

        await asyncio.sleep(interval_s)
