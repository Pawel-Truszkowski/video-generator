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
from app.graph.pipeline import get_event_queue, run_generation, run_planning

# Every route here requires a session by construction, so a future route added to
# this router cannot accidentally be left public. Ownership of a specific job is
# a separate check — see Depends(get_owned_job).
router = APIRouter(dependencies=[Depends(require_user)])

# In-memory state cache for running jobs
_job_states: dict[str, dict] = {}

THUMB_EXTS = (".jpg", ".jpeg", ".png", ".webp")


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
    if job["status"] not in ("uploaded", "error"):
        raise HTTPException(400, f"Job is already in status: {job['status']}")

    # Build state
    upload_dir = os.path.join(settings.data_dir, "uploads", job_id)
    image_paths = sorted(
        os.path.join(upload_dir, f)
        for f in os.listdir(upload_dir)
        if not f.endswith("_processed.jpg")
    )

    state = {
        "job_id": job_id,
        "prompt": job["prompt"],
        "model": job["model"],
        "aspect_ratio": job["aspect_ratio"],
        "target_duration_s": job["target_duration_s"],
        "image_paths": image_paths,
    }

    _job_states[job_id] = state

    # Run planning in background
    asyncio.create_task(run_planning(job_id, state))

    return {"status": "planning"}


@router.post("/jobs/{job_id}/generate")
async def generate_job(job_id: str, job: aiosqlite.Row = Depends(get_owned_job)):
    if job["status"] != "planned":
        raise HTTPException(400, f"Job must be in 'planned' status, got: {job['status']}")

    state = _job_states.get(job_id)
    if not state:
        raise HTTPException(400, "Job state not found — please re-plan")

    # Run generation in background
    asyncio.create_task(run_generation(job_id, state))

    return {"status": "generating"}


@router.post("/jobs/{job_id}/scenes/{idx}/update")
async def update_scene(
    job_id: str,
    idx: int,
    sub_prompt: str = Form(...),
    duration_s: int = Form(...),
    job: aiosqlite.Row = Depends(get_owned_job),
):
    """Allow user to edit a scene's sub_prompt or duration before generation."""
    state = _job_states.get(job_id)
    if not state or not state.get("scenes"):
        raise HTTPException(400, "No plan found for this job")

    if idx < 0 or idx >= len(state["scenes"]):
        raise HTTPException(400, "Invalid scene index")

    state["scenes"][idx]["sub_prompt"] = sub_prompt
    state["scenes"][idx]["duration_s"] = duration_s

    # Recalculate cost
    model = state["model"]
    cost_per_sec = settings.model_costs.get(model, 0.05)
    total_s = sum(s["duration_s"] for s in state["scenes"])
    state["est_cost_usd"] = round(total_s * cost_per_sec, 2)

    # Update DB
    db = await get_db()
    await db.execute(
        "UPDATE scenes SET sub_prompt=?, duration_s=? WHERE job_id=? AND idx=?",
        (sub_prompt, duration_s, job_id, idx),
    )
    await db.execute(
        "UPDATE jobs SET est_cost_usd=? WHERE id=?",
        (state["est_cost_usd"], job_id),
    )
    await db.commit()

    return {"est_cost_usd": state["est_cost_usd"]}


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
    state = _job_states.get(job_id)
    if not state:
        raise HTTPException(400, "Job state not found — please re-plan")

    scenes = [s.model_dump() for s in body.scenes]
    state["scenes"] = scenes
    state["chain_flags"] = [s["chain_from_prev"] for s in scenes]

    # Recalculate cost
    model = state["model"]
    cost_per_sec = settings.model_costs.get(model, 0.05)
    total_s = sum(s["duration_s"] for s in scenes)
    state["est_cost_usd"] = round(total_s * cost_per_sec, 2)

    # Update DB
    db = await get_db()
    await db.execute("DELETE FROM scenes WHERE job_id=?", (job_id,))
    for i, s in enumerate(scenes):
        await db.execute(
            "INSERT INTO scenes (job_id, idx, image_path, sub_prompt, duration_s, chain_from_prev, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (job_id, i, f"image_{s['image_index']}", s["sub_prompt"], s["duration_s"],
             1 if s.get("chain_from_prev") else 0),
        )
    await db.execute(
        "UPDATE jobs SET est_cost_usd=? WHERE id=?",
        (state["est_cost_usd"], job_id),
    )
    await db.commit()

    return {"est_cost_usd": state["est_cost_usd"], "scene_count": len(scenes)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, job: aiosqlite.Row = Depends(get_owned_job)):
    db = await get_db()
    scenes_rows = await db.execute(
        "SELECT * FROM scenes WHERE job_id=? ORDER BY idx", (job_id,)
    )
    scenes = [dict(s) for s in await scenes_rows.fetchall()]

    return {
        "id": job["id"],
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


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, job: aiosqlite.Row = Depends(get_owned_job)):
    queue = get_event_queue(job_id)

    async def event_generator():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                yield {"event": event["type"], "data": json.dumps(event)}
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}

    return EventSourceResponse(event_generator())


@router.get("/media/{job_id}/final.mp4")
async def get_final_video(job: aiosqlite.Row = Depends(get_owned_job)):
    # Path built from job["id"], never the raw path param.
    path = os.path.join(settings.data_dir, "final", f"{job['id']}.mp4")
    if not os.path.exists(path):
        raise HTTPException(404, "Video not found")
    return FileResponse(path, media_type="video/mp4", filename=f"{job['id']}.mp4")
