from __future__ import annotations

import os

from app.config import settings
from app.services.ffmpeg import stitch_clips


async def stitch(state: dict) -> dict:
    """Stitch all clips into the final video."""
    clip_paths = state["clip_paths"]
    chain_flags = state["chain_flags"]
    job_id = state["job_id"]

    out_dir = os.path.join(settings.data_dir, "final")
    os.makedirs(out_dir, exist_ok=True)
    output_path = os.path.join(out_dir, f"{job_id}.mp4")

    await stitch_clips(clip_paths, chain_flags, output_path)

    return {"final_path": output_path, "status": "done"}
