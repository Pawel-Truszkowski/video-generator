from __future__ import annotations

from typing import Protocol


class ProviderError(Exception):
    """A provider failure whose message is fit to show the user.

    Raw provider exceptions are not: fal.ai echoes the whole request body back
    in a 422, base64 image included, and that string would otherwise land in
    scenes.error, the logs and the scene row in the UI.

    `retryable=False` means the provider rejected the input itself (image,
    prompt). The identical request cannot succeed on a second try, so the retry
    loop stops instead of spending its attempts on it.
    """

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


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
