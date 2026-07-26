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
    ) -> str:
        """Generate a video clip from an image. Returns path to the output mp4."""
        ...
