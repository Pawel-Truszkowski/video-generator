from __future__ import annotations

from typing import Protocol


class VideoProvider(Protocol):
    async def generate_clip(
        self,
        image_path: str,
        prompt: str,
        duration_s: int,
        aspect_ratio: str,
        model: str,
        out_path: str,
    ) -> str:
        """Generate a video clip from an image. Returns path to the output mp4.

        The caller owns the filename (`out_path`) and guarantees its directory
        exists — a resume recognises a rendered scene by that path. Implementations
        must overwrite `out_path` if it already exists, so a retry is idempotent.
        """
        ...
