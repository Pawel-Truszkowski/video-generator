"""The DB ⇄ pipeline-state boundary.

Rebuilds pipeline state from the `jobs`/`scenes` tables, so the nodes (validate /
plan_scenes / generate_clips / stitch) keep taking a plain dict.

Representation rule: a scene's clip is described only by its own dict entry
(`clip_path` + `status`). There is deliberately no top-level `clip_paths` list —
two copies of the same fact are two things that can disagree after a crash.
"""

from __future__ import annotations

import os

from app.config import settings
from app.db import get_db

# A job cannot be in one of these statuses while no task is running: they are
# only ever set by a live pipeline. Finding one at startup means the process
# that owned it is gone.
STALE_STATUSES = ("planning", "generating", "stitching")

RAW_EXTS = (".jpg", ".jpeg", ".png", ".webp")
PROCESSED_SUFFIX = "_processed.jpg"


# --- Paths --------------------------------------------------------------------

def uploads_dir(job_id: str) -> str:
    return os.path.join(settings.data_dir, "uploads", job_id)


def clips_dir(job_id: str) -> str:
    return os.path.join(settings.data_dir, "clips", job_id)


def frames_dir(job_id: str) -> str:
    """Last-frame stills for chained scenes. Only chained jobs ever create it."""
    return os.path.join(settings.data_dir, "frames", job_id)


def final_path(job_id: str) -> str:
    """The finished film. Note this is a FILE, not a per-job directory."""
    return os.path.join(settings.data_dir, "final", f"{job_id}.mp4")


def stitch_dir(job_id: str) -> str:
    """Scratch space for stitch(): normalized clips and the ffmpeg concat list.

    Keyed by job_id, and deliberately not under data/final. Shared names there
    meant two concurrent stitches overwrote each other's frames and produced a
    film spliced from both jobs, with no error anywhere (fixed in 0.5.0).
    """
    return os.path.join(settings.data_dir, "stitch", job_id)


def clip_path_for(job_id: str, idx: int) -> str:
    """Where scene `idx` of job `job_id` stores its rendered clip.

    A pure function of (job_id, idx): the file existing at this path is what lets
    a restarted process recognise an already rendered scene, and what makes a
    retry overwrite in place. Callers create clips_dir(job_id) themselves — this
    is also called just to ask "is this scene rendered?", which should not leave
    empty directories behind.

    Zero-padded so an `ls` reads in scene order (scene_10 would sort before
    scene_2 otherwise).
    """
    return os.path.join(clips_dir(job_id), f"scene_{idx:03d}.mp4")


def image_paths_for(job_id: str) -> list[str]:
    """Rebuild the ordered source-image list for a job by listing its upload dir.

    The position in the returned list is the scene's `image_index`: create_job
    writes uploads as 000.jpg, 001.png, … and validate() writes 000_processed.jpg
    next to each, so both sets sort into the original upload order.

    Derived from disk rather than a column: the filesystem is already the
    authority on which images exist, and a cached copy could point at files that
    have since been cleaned up.
    """
    d = uploads_dir(job_id)
    if not os.path.isdir(d):
        return []

    names = os.listdir(d)
    processed = sorted(n for n in names if n.endswith(PROCESSED_SUFFIX))
    raw = sorted(
        n for n in names
        if n.lower().endswith(RAW_EXTS) and not n.endswith(PROCESSED_SUFFIX)
    )

    # All-or-nothing: validate() writes files one by one and bails out on the
    # first bad image, and index == position here — so trusting a partial set
    # would silently drop every image past the failure point.
    if processed and len(processed) == len(raw):
        return [os.path.join(d, n) for n in processed]

    # Raw uploads are the correct fallback, not a workaround: a job in this
    # state resumes from validate(), which overwrites image_paths anyway.
    return [os.path.join(d, n) for n in raw]


# --- Cost ---------------------------------------------------------------------

class UnknownModelError(ValueError):
    """A model with no entry in settings.model_costs cannot be priced."""


def cost_per_second(model: str) -> float:
    """USD per second of video for `model`. Raises on an unknown model.

    Deliberately not `.get(model, 0.05)`: that silently priced anything
    unrecognised at the cheapest model's rate. Harmless while the number was a
    display-only estimate, but from 0.7.0 it is what gets charged, so a typo in
    the model name would hand out video at Wan prices -- or below cost.
    """
    try:
        return settings.model_costs[model]
    except KeyError:
        raise UnknownModelError(f"Brak cennika dla modelu: {model}") from None


def recalc_cost(model: str, scenes: list[dict]) -> float:
    """Single implementation of the cost formula."""
    return round(sum(s["duration_s"] for s in scenes) * cost_per_second(model), 2)


# --- Provider prompt ----------------------------------------------------------

# fal.ai endpoints cap the prompt somewhere between 2000 and 2500 characters,
# depending on the model; the lower bound keeps every one of them happy.
MAX_PROVIDER_PROMPT = 2000


def scene_prompt(sub_prompt: str, style: str) -> str:
    """The prompt a provider receives for one scene.

    `style` is the job-wide style of a manual plan ('' for an LLM plan, whose
    `jobs.prompt` is a film description, not something to repeat per scene).
    Appended rather than prepended: video models weight the start of the prompt
    most, and the scene's own motion is what differs between clips.
    """
    sub_prompt = (sub_prompt or "").strip()
    style = (style or "").strip()
    if not style:
        return sub_prompt[:MAX_PROVIDER_PROMPT]
    return f"{sub_prompt.rstrip('. ')}. {style}"[:MAX_PROVIDER_PROMPT]


# --- Rendered-clip check ------------------------------------------------------

def is_rendered(job_id: str, idx: int, scene: dict) -> bool:
    """True when scene `idx` already has a usable clip and needs no provider call.

    Lives here rather than inside generate_clips because two callers need the
    same answer for different reasons: the pipeline skips the scene, and the API
    layer prices a resume/retry from "how many scenes are actually left". Two
    copies of the rule would drift, and this one decides what the user pays.
    """
    if scene.get("status") != "done":
        return False
    out_path = clip_path_for(job_id, idx)
    # The disk gets the final word. `scene["clip_path"]` is deliberately not
    # consulted: older rows point at a uuid name that stitch would never find.
    if not os.path.exists(out_path):
        return False
    # A crashed download or a killed ffmpeg leaves a 0-byte file behind.
    return os.path.getsize(out_path) > 0


# --- Read ---------------------------------------------------------------------

async def load_state(job_id: str) -> dict | None:
    """Rebuild pipeline state for a job. None when the job does not exist.

    Does NOT check ownership — every caller must have resolved the job through
    Depends(get_owned_job) first, which is the single authorization code path.
    """
    db = await get_db()

    cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job = await cur.fetchone()
    if job is None:
        return None

    cur = await db.execute(
        "SELECT idx, image_index, sub_prompt, duration_s, chain_from_prev, status, "
        "       clip_path, error "
        "  FROM scenes WHERE job_id = ? ORDER BY idx",
        (job_id,),
    )
    scenes = [
        {
            "image_index": r["image_index"],
            "sub_prompt": r["sub_prompt"],
            "duration_s": r["duration_s"],
            "chain_from_prev": bool(r["chain_from_prev"]),
            "status": r["status"],
            "clip_path": r["clip_path"],
            "error": r["error"],
        }
        for r in await cur.fetchall()
    ]

    est_cost = job["est_cost_usd"]
    if est_cost is None and scenes:
        est_cost = recalc_cost(job["model"], scenes)

    return {
        "job_id": job["id"],
        "prompt": job["prompt"],
        "plan_mode": job["plan_mode"],
        "model": job["model"],
        "aspect_ratio": job["aspect_ratio"],
        "target_duration_s": job["target_duration_s"],
        "image_paths": image_paths_for(job["id"]),
        "scenes": scenes,
        # stitch() reads chain_flags; kept aligned with `scenes` by construction.
        "chain_flags": [s["chain_from_prev"] for s in scenes],
        "est_cost_usd": est_cost or 0.0,
        "status": job["status"],
    }


# --- Write --------------------------------------------------------------------

async def save_scenes(job_id: str, scenes: list[dict]) -> None:
    """Replace a job's scene list.

    Resets status/clip_path: a changed plan invalidates rendered clips, and clip
    files are named by scene index, so a reorder would otherwise leave a clip
    attached to a scene it was not rendered for. Callers must therefore refuse
    plan edits once generation has started.
    """
    db = await get_db()
    await db.execute("DELETE FROM scenes WHERE job_id = ?", (job_id,))
    for i, s in enumerate(scenes):
        await db.execute(
            "INSERT INTO scenes (job_id, idx, image_index, sub_prompt, duration_s, "
            "                    chain_from_prev, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (
                job_id,
                i,
                s["image_index"],
                s["sub_prompt"],
                s["duration_s"],
                1 if s.get("chain_from_prev") else 0,
            ),
        )
    await db.commit()


async def update_scene_fields(
    job_id: str, idx: int, sub_prompt: str, duration_s: int, image_index: int
) -> None:
    """Targeted edit of one scene (no DELETE, so clip_path/status survive).

    `image_index` is a per-scene field like the other two, so it belongs on this
    path rather than on save_scenes: swapping a scene's source image must not
    renumber the list or drop what the other scenes have already rendered.
    """
    db = await get_db()
    await db.execute(
        "UPDATE scenes SET sub_prompt = ?, duration_s = ?, image_index = ? "
        " WHERE job_id = ? AND idx = ?",
        (sub_prompt, duration_s, image_index, job_id, idx),
    )
    await db.commit()


async def set_job_cost(job_id: str, est_cost_usd: float) -> None:
    db = await get_db()
    await db.execute("UPDATE jobs SET est_cost_usd = ? WHERE id = ?", (est_cost_usd, job_id))
    await db.commit()


async def reset_scene(job_id: str, idx: int) -> None:
    """Put one scene back to 'pending' so the next run regenerates just it.

    Clearing `status` is what flips already_rendered() to False for this scene
    while every other one still short-circuits.
    """
    db = await get_db()
    await db.execute(
        "UPDATE scenes SET status = 'pending', clip_path = NULL, error = NULL "
        " WHERE job_id = ? AND idx = ?",
        (job_id, idx),
    )
    await db.commit()


async def mark_scene_done(job_id: str, idx: int, clip_path: str) -> None:
    """Persist one finished clip. This is what makes generation resumable."""
    db = await get_db()
    await db.execute(
        # error = NULL: a scene that succeeds on retry must not keep the message
        # from the attempt that failed, or the UI would still flag it as broken.
        "UPDATE scenes SET status = 'done', clip_path = ?, error = NULL "
        " WHERE job_id = ? AND idx = ?",
        (clip_path, job_id, idx),
    )
    await db.commit()


async def mark_scene_error(job_id: str, idx: int, error: str) -> None:
    """Record why one scene failed, so the UI can offer a targeted retry."""
    print(f"[job {job_id}] scene {idx} failed: {error}")
    db = await get_db()
    await db.execute(
        "UPDATE scenes SET status = 'error', error = ? WHERE job_id = ? AND idx = ?",
        (error, job_id, idx),
    )
    await db.commit()


async def reconcile_interrupted() -> list[str]:
    """Startup sweep: flag jobs whose pipeline died with the previous process.

    Safe because the app runs a single uvicorn worker in a single container, so
    at startup nothing can legitimately be mid-flight.

    Returns the ids it touched, not a count: each of those jobs may hold a
    credit reservation that died with the task, and the caller settles them.
    Ids rather than a settle call here, because credits imports this module for
    the cost formula -- the dependency has to point one way.
    """
    db = await get_db()
    placeholders = ", ".join("?" * len(STALE_STATUSES))
    cur = await db.execute(
        f"SELECT id FROM jobs WHERE status IN ({placeholders})", STALE_STATUSES
    )
    job_ids = [r["id"] for r in await cur.fetchall()]
    if not job_ids:
        return []

    await db.execute(
        f"UPDATE jobs SET status = 'interrupted' WHERE status IN ({placeholders})",
        STALE_STATUSES,
    )
    await db.commit()
    return job_ids
