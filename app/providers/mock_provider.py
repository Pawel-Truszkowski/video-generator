from __future__ import annotations

import asyncio
import os
import subprocess
import uuid

from app.config import settings


class MockProvider:
    """Generates a short test clip with FFmpeg (color bars + text) instead of calling fal.ai."""

    async def generate_clip(
        self,
        image_path: str,
        prompt: str,
        duration_s: int,
        aspect_ratio: str,
        model: str,
    ) -> str:
        out_dir = os.path.join(settings.data_dir, "clips")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{uuid.uuid4().hex}.mp4")

        # Use the input image as background, overlay with prompt text
        short_prompt = prompt[:60].replace("'", "")
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-t", str(duration_s),
            "-vf", (
                f"scale={settings.output_width}:{settings.output_height}:force_original_aspect_ratio=decrease,"
                f"pad={settings.output_width}:{settings.output_height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"drawtext=text='{short_prompt}':fontsize=24:fontcolor=white:"
                f"x=(w-text_w)/2:y=h-60:box=1:boxcolor=black@0.6:boxborderw=8"
            ),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", str(settings.output_fps),
            "-an", out_path,
        ]

        # Simulate some processing time
        await asyncio.sleep(2)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg mock clip failed: {stderr.decode()}")

        return out_path
