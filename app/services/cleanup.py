"""File retention and job deletion (roadmap Faza 1.3).

The only place in the app that deletes a job's files. Every path comes from
`job_state`; building paths here instead would drift from what the pipeline
actually wrote, and half the data would quietly never be swept.

`data/final` is exempt from retention and only purge_all() (i.e. DELETE
/jobs/{id}) removes it: the finished film is a product someone paid for.
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

# Directories holding one subdirectory per job_id. `final` is absent on purpose:
# every file there has an owner and must not be deleted on age alone.
WORK_SUBDIRS = ("uploads", "frames", "clips", "stitch")


def _rmtree(path: str) -> bool:
    """Delete a directory. True if anything was removed.

    An I/O error must not abort the whole sweep: one locked directory would leave
    everything else unswept, and the next attempt is a day away.
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
    """Delete a job's working material. Returns how many directories went."""
    removed = 0
    for path in (uploads_dir(job_id), frames_dir(job_id), clips_dir(job_id),
                 stitch_dir(job_id)):
        removed += _rmtree(path)
    return removed


def purge_all(job_id: str) -> int:
    """purge_workdirs() plus the finished film."""
    return purge_workdirs(job_id) + _remove_file(final_path(job_id))


# --- Sweep --------------------------------------------------------------------

def _never_running(_job_id: str) -> bool:
    return False


async def _purge_old_jobs(is_running: Callable[[str], bool]) -> int:
    """Retention for jobs the DB knows about. Returns how many were swept."""
    db = await get_db()
    placeholders = ", ".join("?" * len(STALE_STATUSES))
    cur = await db.execute(
        # Two status filters, patching different holes. The `status` column
        # covers the post-restart case; `is_running` covers normal operation,
        # where a live task holds an active status and is writing to clips/.
        # Deleting files under a running task yields half a film and no error.
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
        # The marker is written even when nothing was deleted: "this job's
        # working material no longer exists" is equally true, and without the
        # write the sweep would reconsider the same job every day forever.
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
    """False when mtime cannot be read either — never delete blind."""
    try:
        return os.path.getmtime(path) < cutoff
    except OSError:
        return False


async def _purge_orphans() -> int:
    """Delete working material no job claims.

    A loose file at the top of these directories is an orphan by definition: the
    current code always writes into a per-job_id subdirectory (hence pre-0.3.0
    leftovers named uuid4().mp4 and unfinished `*.part` files).

    The age threshold is required, not cautious: `create_job` makes
    uploads/{job_id} BEFORE the INSERT, so a job mid-upload looks exactly like an
    orphan for a moment.
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
    """One retention pass. Returns counters for the log."""
    jobs = await _purge_old_jobs(is_running)
    orphans = await _purge_orphans()
    return {"jobs": jobs, "orphans": orphans}


async def retention_loop(is_running: Callable[[str], bool] = _never_running) -> None:
    """Sweep at startup, then every `settings.cleanup_interval_h` hours.

    The first pass runs immediately, which makes restarting the container a
    legitimate way to force a cleanup, testing included.
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
            # The loop must survive a failed pass. An exception here would end
            # the task silently and cleanup would stop until the next restart,
            # with no signal but a growing disk.
            print(f"[cleanup] sweep nieudany: {type(exc).__name__}: {exc}")

        await asyncio.sleep(interval_s)
