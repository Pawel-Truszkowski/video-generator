from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import uuid

import aiosqlite
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.auth.deps import get_owned_job, require_user
from app.config import settings
from app.db import get_db
from app.graph.pipeline import (
    get_event_queue,
    release_event_queue,
    run_generation,
    run_planning,
)
from app.services import job_state

# Every route here requires a session by construction, so a future route added to
# this router cannot accidentally be left public. Ownership of a specific job is
# a separate check — see Depends(get_owned_job).
router = APIRouter(dependencies=[Depends(require_user)])

# Ids of jobs with a live asyncio task. Losing this on restart is correct
# rather than a bug: after a restart nothing is running.
_running: set[str] = set()

THUMB_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# Editing the plan rewrites the scenes table, and clips are named by scene
# index — a reorder mid-generation would misattach an already rendered clip.
BLOCKED_EDIT_STATUSES = ("generating", "stitching", "done")

# Retrying one scene spends money, so this is an allowlist: a status added later
# is refused until someone decides it should not be. 'planned'/'uploaded' have no
# clips to retry (that is POST /generate), and 'done' would overwrite a finished
# film — both go through their own endpoints instead.
RETRYABLE_JOB_STATUSES = ("error", "interrupted")


def _start(job_id: str, coro) -> None:
    """Run a pipeline coroutine in the background, once per job.

    Closes the rejected coroutine: an un-awaited one leaks and warns at GC time.
    """
    if job_id in _running:
        coro.close()
        raise HTTPException(409, "Ten job już się wykonuje")

    _running.add(job_id)

    async def _runner():
        try:
            await coro
        finally:
            _running.discard(job_id)

    asyncio.create_task(_runner())


def _assert_plan_editable(job: aiosqlite.Row, scenes: list[dict]) -> None:
    if job["status"] in BLOCKED_EDIT_STATUSES:
        raise HTTPException(409, f"Nie można edytować planu w statusie: {job['status']}")
    # Status alone is not enough: a job interrupted mid-generation sits in
    # 'interrupted', yet its finished clips are real and paid for.
    if any(s.get("status") == "done" for s in scenes):
        raise HTTPException(
            409, "Część scen jest już wygenerowana — edycja skasowałaby gotowe klipy"
        )


@router.post("/jobs")
async def create_job(
    images: list[UploadFile] = File(...),
    prompt: str = Form(...),
    model: str = Form("wan"),
    aspect_ratio: str = Form("16:9"),
    target_duration_s: int = Form(120),
    user: aiosqlite.Row = Depends(require_user),
):
    job_id = uuid.uuid4().hex[:12]
    upload_dir = os.path.join(settings.data_dir, "uploads", job_id)
    os.makedirs(upload_dir, exist_ok=True)

    image_paths = []
    for img in images:
        ext = os.path.splitext(img.filename or "img.jpg")[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            raise HTTPException(400, f"Unsupported image format: {ext}")
        file_path = os.path.join(upload_dir, f"{len(image_paths):03d}{ext}")
        content = await img.read()
        with open(file_path, "wb") as f:
            f.write(content)
        image_paths.append(file_path)

    if not image_paths:
        raise HTTPException(400, "No images provided")

    db = await get_db()
    await db.execute(
        "INSERT INTO jobs (id, status, prompt, model, aspect_ratio, target_duration_s, "
        "user_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
        (job_id, "uploaded", prompt, model, aspect_ratio, target_duration_s, user["id"]),
    )
    await db.commit()

    return {"job_id": job_id}


@router.get("/jobs")
async def list_jobs(
    user: aiosqlite.Row = Depends(require_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Read-only list for the 'Moje filmy' screen."""
    db = await get_db()
    rows = await db.execute(
        "SELECT j.id, j.status, j.prompt, j.model, j.est_cost_usd, j.created_at, j.error, "
        "       COUNT(s.id) AS scene_count, "
        "       COALESCE(SUM(s.duration_s), 0) AS total_duration_s "
        "  FROM jobs j "
        "  LEFT JOIN scenes s ON s.job_id = j.id "
        " WHERE j.user_id = ? "
        " GROUP BY j.id "
        " ORDER BY j.created_at DESC, j.id DESC "
        " LIMIT ? OFFSET ?",
        (user["id"], limit, offset),
    )

    jobs = []
    for row in await rows.fetchall():
        job = dict(row)
        job["thumb_url"] = f"/jobs/{row['id']}/thumbnail"
        # stat per row (cheap at limit<=200) so the UI never renders a download
        # button for a job whose mp4 has been cleaned off disk.
        final_path = os.path.join(settings.data_dir, "final", f"{row['id']}.mp4")
        job["video_url"] = (
            f"/media/{row['id']}/final.mp4"
            if row["status"] == "done" and os.path.exists(final_path)
            else None
        )
        jobs.append(job)

    return {"jobs": jobs}


@router.post("/jobs/{job_id}/plan")
async def plan_job(job_id: str, job: aiosqlite.Row = Depends(get_owned_job)):
    if job["status"] not in ("uploaded", "error", "interrupted"):
        raise HTTPException(400, f"Job is already in status: {job['status']}")

    state = await job_state.load_state(job_id)
    if state is None:
        raise HTTPException(404, "Job not found")
    if not state["image_paths"]:
        raise HTTPException(400, "Brak zdjęć dla tego joba")

    _start(job_id, run_planning(job_id, state))

    return {"status": "planning"}


@router.post("/jobs/{job_id}/generate")
async def generate_job(job_id: str, job: aiosqlite.Row = Depends(get_owned_job)):
    if job["status"] != "planned":
        raise HTTPException(400, f"Job must be in 'planned' status, got: {job['status']}")

    state = await job_state.load_state(job_id)
    if state is None or not state["scenes"]:
        raise HTTPException(400, "Brak planu dla tego joba")

    _start(job_id, run_generation(job_id, state))

    return {"status": "generating"}


@router.post("/jobs/{job_id}/scenes/{idx}/update")
async def update_scene(
    job_id: str,
    idx: int,
    sub_prompt: str = Form(...),
    duration_s: int = Form(...),
    image_index: int = Form(...),
    job: aiosqlite.Row = Depends(get_owned_job),
):
    """Allow user to edit a scene's sub_prompt, duration or source image."""
    state = await job_state.load_state(job_id)
    if state is None or not state["scenes"]:
        raise HTTPException(400, "Brak planu dla tego joba")

    _assert_plan_editable(job, state["scenes"])

    if idx < 0 or idx >= len(state["scenes"]):
        raise HTTPException(400, "Invalid scene index")

    # Rejected here rather than in generate_clips: an out-of-range index would
    # otherwise reach image_paths[...] inside a background task, surfacing to the
    # user as a bare "error" status instead of a 400 on the request that caused it.
    if image_index < 0 or image_index >= len(state["image_paths"]):
        raise HTTPException(400, f"Nieprawidłowy indeks zdjęcia: {image_index}")

    state["scenes"][idx]["sub_prompt"] = sub_prompt
    state["scenes"][idx]["duration_s"] = duration_s
    state["scenes"][idx]["image_index"] = image_index

    # Targeted UPDATE, not save_scenes: rewriting the whole list would reset
    # every scene's status/clip_path for a one-field edit.
    await job_state.update_scene_fields(job_id, idx, sub_prompt, duration_s, image_index)

    est_cost = job_state.recalc_cost(state["model"], state["scenes"])
    await job_state.set_job_cost(job_id, est_cost)

    return {"est_cost_usd": est_cost}


class SceneItem(BaseModel):
    image_index: int
    sub_prompt: str
    duration_s: int
    chain_from_prev: bool = False


class SyncScenesRequest(BaseModel):
    scenes: list[SceneItem]


@router.post("/jobs/{job_id}/scenes/sync")
async def sync_scenes(
    job_id: str,
    body: SyncScenesRequest,
    job: aiosqlite.Row = Depends(get_owned_job),
):
    """Replace the entire scene list (add/remove/reorder scenes)."""
    state = await job_state.load_state(job_id)
    if state is None:
        raise HTTPException(404, "Job not found")

    _assert_plan_editable(job, state["scenes"])

    scenes = [s.model_dump() for s in body.scenes]

    image_count = len(state["image_paths"])
    for s in scenes:
        if s["image_index"] < 0 or s["image_index"] >= image_count:
            raise HTTPException(400, f"Nieprawidłowy indeks zdjęcia: {s['image_index']}")

    await job_state.save_scenes(job_id, scenes)

    est_cost = job_state.recalc_cost(state["model"], scenes)
    await job_state.set_job_cost(job_id, est_cost)

    return {"est_cost_usd": est_cost, "scene_count": len(scenes)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, job: aiosqlite.Row = Depends(get_owned_job)):
    db = await get_db()
    scenes_rows = await db.execute(
        "SELECT * FROM scenes WHERE job_id=? ORDER BY idx", (job_id,)
    )
    scenes = [dict(s) for s in await scenes_rows.fetchall()]

    return {
        "id": job["id"],
        # The plan editor rebuilds its image picker from this after a restart.
        "image_count": len(job_state.image_paths_for(job["id"])),
        "status": job["status"],
        "prompt": job["prompt"],
        "model": job["model"],
        "aspect_ratio": job["aspect_ratio"],
        "target_duration_s": job["target_duration_s"],
        "est_cost_usd": job["est_cost_usd"],
        "created_at": job["created_at"],
        "error": job["error"],
        "scenes": scenes,
    }


@router.get("/jobs/{job_id}/thumbnail")
async def get_job_thumbnail(job: aiosqlite.Row = Depends(get_owned_job)):
    """First uploaded image, for the 'Moje filmy' list.

    Serves from data/uploads/ rather than data/frames/ because uploads exist for
    every job while frames only exist for chained ones. The directory is built
    from job["id"] (a DB value) and no filename ever comes from the client, so
    there is no traversal surface.
    """
    upload_dir = os.path.join(settings.data_dir, "uploads", job["id"])
    if not os.path.isdir(upload_dir):
        raise HTTPException(404, "Brak miniaturki")

    entries = sorted(f for f in os.listdir(upload_dir) if f.lower().endswith(THUMB_EXTS))
    if not entries:
        raise HTTPException(404, "Brak miniaturki")

    # Prefer the letterboxed JPEG written by graph/nodes/validate.py.
    processed = [f for f in entries if f.endswith("_processed.jpg")]
    path = os.path.join(upload_dir, processed[0] if processed else entries[0])

    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path)[0] or "image/jpeg",
        # "private" stops a shared proxy serving one user's thumbnail to another.
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/jobs/{job_id}/images/{index}")
async def get_job_image(index: int, job: aiosqlite.Row = Depends(get_owned_job)):
    """One source image by scene `image_index`, for the rebuilt plan editor.

    Traversal-free like get_job_thumbnail: the directory comes from job["id"] and
    the file is picked positionally, so no client string reaches the filesystem.
    """
    paths = job_state.image_paths_for(job["id"])
    if index < 0 or index >= len(paths):
        raise HTTPException(404, "Brak zdjęcia")

    path = paths[index]
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path)[0] or "image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


def _assert_scene_retryable(job: aiosqlite.Row, scene: dict) -> None:
    """Refuse a retry that makes no sense, with a 409.

    `job["status"]` is the job's state ('uploaded' / 'planned' / 'generating' /
    'stitching' / 'interrupted' / 'error' / 'done'); `scene["status"]` is one of
    'pending' / 'done' / 'error'.
    """
    if job["status"] not in RETRYABLE_JOB_STATUSES:
        raise HTTPException(409, f"Nie można powtórzyć sceny w statusie joba: {job['status']}")
    if scene.get("status") != "error":
        raise HTTPException(409, f"Nie można powtórzyć sceny w statusie: {scene.get('status')}")


@router.post("/jobs/{job_id}/scenes/{idx}/retry")
async def retry_scene(job_id: str, idx: int, job: aiosqlite.Row = Depends(get_owned_job)):
    """Regenerate one failed scene, then carry the job on to stitching.

    No dedicated generation path: clearing this row makes already_rendered()
    return False for this scene and True for every other, so the ordinary
    run_generation refills exactly the one gap. A scene later in the same chain
    needs no cascade either — process_chain is sequential, so anything after a
    failure never ran and is still 'pending'.
    """
    state = await job_state.load_state(job_id)
    if state is None or not state["scenes"]:
        raise HTTPException(400, "Brak planu dla tego joba")
    if idx < 0 or idx >= len(state["scenes"]):
        raise HTTPException(400, "Invalid scene index")

    # Checked before the reset, not by letting _start() raise: refusing after
    # the wipe would leave the scene cleared with nothing running to refill it.
    if job_id in _running:
        raise HTTPException(409, "Ten job już się wykonuje")

    _assert_scene_retryable(job, state["scenes"][idx])

    await job_state.reset_scene(job_id, idx)
    # The status reset alone would be enough for already_rendered(), but a
    # half-written file from a cancelled attempt must not outlive it.
    clip = job_state.clip_path_for(job_id, idx)
    for path in (clip, f"{clip}.part"):
        if os.path.exists(path):
            os.remove(path)

    state = await job_state.load_state(job_id)
    _start(job_id, run_generation(job_id, state))

    return {"status": "generating", "idx": idx}


def _decide_stage(job: aiosqlite.Row, scenes: list[dict]) -> str:
    """Which pipeline stage a job should resume from.

    Returns one of:
      "planning"   — run validate + plan_scenes from scratch (no usable plan)
      "planned"    — a plan exists; the user reviews it and presses Generuj
      "generating" — resume generation (already-rendered scenes get skipped)
      "done"       — nothing to do, the final mp4 exists

    Only the first and third start a background task; the others just tell the
    frontend which screen to show.
    """
    if job["status"] == "done":
        return "done"

    if not scenes:
        return "planning"

    if job["status"] in ("uploaded", "planned"):
        return "planned"

    return "generating"


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str, job: aiosqlite.Row = Depends(get_owned_job)):
    """Restart an interrupted job from its last completed step."""
    if job_id in _running:
        raise HTTPException(409, "Ten job już się wykonuje")

    state = await job_state.load_state(job_id)
    if state is None:
        raise HTTPException(404, "Job not found")

    stage = _decide_stage(job, state["scenes"])

    if stage == "planning":
        await _set_status(job_id, "uploaded")
        _start(job_id, run_planning(job_id, state))
    elif stage == "generating":
        _start(job_id, run_generation(job_id, state))
    # "planned"/"done" need no task — the frontend just opens the right screen.

    return {"stage": stage}


async def _set_status(job_id: str, status: str) -> None:
    db = await get_db()
    await db.execute("UPDATE jobs SET status=?, error=NULL WHERE id=?", (status, job_id))
    await db.commit()


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, job: aiosqlite.Row = Depends(get_owned_job)):
    queue = get_event_queue(job_id)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {"event": event["type"], "data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            # A disconnecting client cancels this generator, so `finally` is the
            # only place guaranteed to run — code after the loop never would.
            release_event_queue(job_id, queue)

    return EventSourceResponse(event_generator())


@router.get("/media/{job_id}/final.mp4")
async def get_final_video(job: aiosqlite.Row = Depends(get_owned_job)):
    # Path built from job["id"], never the raw path param.
    path = os.path.join(settings.data_dir, "final", f"{job['id']}.mp4")
    if not os.path.exists(path):
        raise HTTPException(404, "Video not found")
    return FileResponse(path, media_type="video/mp4", filename=f"{job['id']}.mp4")
