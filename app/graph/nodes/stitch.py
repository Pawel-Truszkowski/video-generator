from __future__ import annotations

import os

from app.services.ffmpeg import stitch_clips
from app.services.job_state import final_path, stitch_dir


async def stitch(state: dict) -> dict:
    """Stitch all clips into the final video."""
    clip_paths = state["clip_paths"]
    chain_flags = state["chain_flags"]
    job_id = state["job_id"]

    output_path = final_path(job_id)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    await stitch_clips(clip_paths, chain_flags, output_path, stitch_dir(job_id))

    return {"final_path": output_path, "status": "done"}
