from __future__ import annotations

import asyncio
import subprocess

from app.config import settings


def _escape_drawtext(text: str) -> str:
    """Make `text` safe to drop inside drawtext=text='...' in a filtergraph.

    FFmpeg unescapes a filtergraph in two passes and the quotes only survive the
    first, so a bare `:` still reaches the option parser and ends the drawtext
    argument early. Quoting alone and backslashes alone both fail; only the two
    together work (checked on ffmpeg 7.1).

    Apostrophes are dropped rather than escaped — closing the quote, emitting an
    escaped one and reopening is more machinery than a mock caption deserves.
    """
    return (
        text.replace("\\", "\\\\")   # first, so later backslashes are not doubled again
            .replace("'", "")
            .replace(":", "\\:")
            .replace("%", "\\%")   # drawtext expands %{...} sequences
    )


class MockProvider:
    """Generates a short test clip with FFmpeg (color bars + text) instead of calling fal.ai."""

    async def generate_clip(
        self,
        image_path: str,
        prompt: str,
        duration_s: int,
        aspect_ratio: str,
        model: str,
        out_path: str,
    ) -> str:
        # Use the input image as background, overlay with prompt text
        short_prompt = _escape_drawtext(prompt[:60])
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
