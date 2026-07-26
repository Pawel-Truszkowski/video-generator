from __future__ import annotations

import asyncio
import traceback

from app.db import get_db
from app.graph.nodes.validate import validate
from app.graph.nodes.plan_scenes import plan_scenes
from app.graph.nodes.generate_clips import generate_clips
from app.graph.nodes.stitch import stitch

# SSE event bus — job_id → asyncio.Queue
_event_queues: dict[str, list[asyncio.Queue]] = {}


def get_event_queue(job_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _event_queues.setdefault(job_id, []).append(q)
    return q


def _emit(job_id: str, event: dict):
    for q in _event_queues.get(job_id, []):
        q.put_nowait(event)


async def _update_job_status(job_id: str, status: str, error: str | None = None,
                              est_cost: float | None = None):
    db = await get_db()
    if error:
        await db.execute("UPDATE jobs SET status=?, error=? WHERE id=?", (status, error, job_id))
    elif est_cost is not None:
        await db.execute("UPDATE jobs SET status=?, est_cost_usd=? WHERE id=?", (status, est_cost, job_id))
    else:
        await db.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    await db.commit()


async def _save_scenes(job_id: str, scenes: list[dict]):
    db = await get_db()
    await db.execute("DELETE FROM scenes WHERE job_id=?", (job_id,))
    for i, s in enumerate(scenes):
        await db.execute(
            "INSERT INTO scenes (job_id, idx, image_path, sub_prompt, duration_s, chain_from_prev, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (job_id, i, f"image_{s['image_index']}", s["sub_prompt"], s["duration_s"],
             1 if s.get("chain_from_prev") else 0),
        )
    await db.commit()


async def run_planning(job_id: str, state: dict) -> dict:
    """Run validate + plan_scenes. Returns updated state."""
    try:
        _emit(job_id, {"type": "status", "status": "validating"})
        result = await validate(state)
        state.update(result)

        if state.get("status") == "error":
            await _update_job_status(job_id, "error", state.get("error"))
            _emit(job_id, {"type": "error", "error": state["error"]})
            return state

        _emit(job_id, {"type": "status", "status": "planning"})
        await _update_job_status(job_id, "planning")

        result = await plan_scenes(state)
        state.update(result)

        await _update_job_status(job_id, "planned", est_cost=state["est_cost_usd"])
        await _save_scenes(job_id, state["scenes"])

        _emit(job_id, {
            "type": "planned",
            "scenes": state["scenes"],
            "est_cost_usd": state["est_cost_usd"],
        })

        return state

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        await _update_job_status(job_id, "error", error_msg)
        _emit(job_id, {"type": "error", "error": error_msg})
        traceback.print_exc()
        return {**state, "status": "error", "error": error_msg}


async def run_generation(job_id: str, state: dict) -> dict:
    """Run generate_clips + stitch + finalize. Called after user approves plan."""
    try:
        _emit(job_id, {"type": "status", "status": "generating"})
        await _update_job_status(job_id, "generating")

        result = await generate_clips(state)
        state.update(result)

        _emit(job_id, {"type": "status", "status": "stitching"})
        await _update_job_status(job_id, "stitching")

        result = await stitch(state)
        state.update(result)

        await _update_job_status(job_id, "done")
        _emit(job_id, {
            "type": "done",
            "final_path": f"/media/{job_id}/final.mp4",
        })

        return state

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        await _update_job_status(job_id, "error", error_msg)
        _emit(job_id, {"type": "error", "error": error_msg})
        traceback.print_exc()
        return {**state, "status": "error", "error": error_msg}
