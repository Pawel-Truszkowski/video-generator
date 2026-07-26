from __future__ import annotations

import asyncio
import json
import os
import uuid

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.db import get_db
from app.graph.pipeline import get_event_queue, run_generation, run_planning

router = APIRouter()

# In-memory state cache for running jobs
_job_states: dict[str, dict] = {}


@router.post("/jobs")
async def create_job(
    images: list[UploadFile] = File(...),
    prompt: str = Form(...),
    model: str = Form("wan"),
    aspect_ratio: str = Form("16:9"),
    target_duration_s: int = Form(120),
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
        "INSERT INTO jobs (id, status, prompt, model, aspect_ratio, target_duration_s) VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, "uploaded", prompt, model, aspect_ratio, target_duration_s),
    )
    await db.commit()

    return {"job_id": job_id}


@router.post("/jobs/{job_id}/plan")
async def plan_job(job_id: str):
    db = await get_db()
    row = await db.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
    job = await row.fetchone()
    if not job:
        raise HTTPException(404, "Job not found")
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
async def generate_job(job_id: str):
    db = await get_db()
    row = await db.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
    job = await row.fetchone()
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "planned":
        raise HTTPException(400, f"Job must be in 'planned' status, got: {job['status']}")

    state = _job_states.get(job_id)
    if not state:
        raise HTTPException(400, "Job state not found — please re-plan")

    # Run generation in background
    asyncio.create_task(run_generation(job_id, state))

    return {"status": "generating"}


@router.post("/jobs/{job_id}/scenes/{idx}/update")
async def update_scene(job_id: str, idx: int, sub_prompt: str = Form(...), duration_s: int = Form(...)):
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
async def sync_scenes(job_id: str, body: SyncScenesRequest):
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
async def get_job(job_id: str):
    db = await get_db()
    row = await db.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
    job = await row.fetchone()
    if not job:
        raise HTTPException(404, "Job not found")

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
        "error": job["error"],
        "scenes": scenes,
    }


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
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
async def get_final_video(job_id: str):
    path = os.path.join(settings.data_dir, "final", f"{job_id}.mp4")
    if not os.path.exists(path):
        raise HTTPException(404, "Video not found")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")
