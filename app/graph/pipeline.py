from __future__ import annotations

import asyncio
import traceback

from app.db import get_db
from app.graph.nodes.validate import validate
from app.graph.nodes.plan_scenes import plan_scenes
from app.graph.nodes.generate_clips import generate_clips
from app.graph.nodes.stitch import stitch
from app.services import job_state

# SSE event bus — job_id → one queue per connected subscriber.
_event_queues: dict[str, list[asyncio.Queue]] = {}

# Bounded so a subscriber that stopped reading cannot grow without limit. Sized
# far above a normal job (a handful of events per scene): hitting it means a
# client is gone, not that the job is busy.
EVENT_QUEUE_MAXSIZE = 100


def get_event_queue(job_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAXSIZE)
    _event_queues.setdefault(job_id, []).append(q)
    return q


def release_event_queue(job_id: str, q: asyncio.Queue) -> None:
    """Unsubscribe one client. Must run when its SSE generator ends.

    Drops the job_id key once the last subscriber leaves — otherwise the dict
    grows one dead entry per job for the lifetime of the process.
    """
    queues = _event_queues.get(job_id)
    if not queues:
        return
    try:
        queues.remove(q)
    except ValueError:
        return
    if not queues:
        del _event_queues[job_id]


def _emit(job_id: str, event: dict):
    # Iterate a copy: handling a full queue may unsubscribe it mid-loop.
    for q in list(_event_queues.get(job_id, [])):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # A full queue means this subscriber stopped draining it, so drop it:
            # skipping the event would leave the queue full forever, and the
            # browser's EventSource reconnects on its own.
            print(f"[job {job_id}] SSE subscriber not reading — dropped")
            release_event_queue(job_id, q)


async def _update_job_status(job_id: str, status: str, error: str | None = None,
                              est_cost: float | None = None):
    db = await get_db()
    # Every non-error transition clears `error`: a job that later succeeds must
    # not keep the message from the run that failed, or the UI shows a finished
    # film flagged as broken.
    if error:
        await db.execute("UPDATE jobs SET status=?, error=? WHERE id=?", (status, error, job_id))
    elif est_cost is not None:
        await db.execute(
            "UPDATE jobs SET status=?, est_cost_usd=?, error=NULL WHERE id=?",
            (status, est_cost, job_id),
        )
    else:
        await db.execute("UPDATE jobs SET status=?, error=NULL WHERE id=?", (status, job_id))
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
        await job_state.save_scenes(job_id, state["scenes"])

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

        total = len(state["scenes"])

        async def on_scene_done(idx: int, clip_path: str):
            # Persisting per scene — not after the whole node — is what makes
            # the run resumable: a crash on scene 5 keeps 0-4 recorded as done.
            await job_state.mark_scene_done(job_id, idx, clip_path)
            done = sum(1 for s in state["scenes"] if s.get("status") == "done")
            _emit(job_id, {
                "type": "scene", "idx": idx, "status": "done",
                "done": done, "total": total,
            })

        async def on_scene_error(idx: int, error: str):
            await job_state.mark_scene_error(job_id, idx, error)
            _emit(job_id, {"type": "scene", "idx": idx, "status": "error", "error": error})

        result = await generate_clips(state, on_scene_done, on_scene_error)
        state.update(result)

        # Only stitch a complete set. generate_clips returns clip_paths with the
        # gaps squeezed out, so one missing scene would shift the list against
        # chain_flags and stitch_clips would crossfade the wrong pairs.
        missing = [i for i, s in enumerate(state["scenes"]) if s.get("status") != "done"]
        if missing:
            msg = f"Nie wygenerowano scen: {', '.join(str(i + 1) for i in missing)}"
            await _update_job_status(job_id, "error", msg)
            _emit(job_id, {"type": "error", "error": msg})
            return {**state, "status": "error", "error": msg}

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
