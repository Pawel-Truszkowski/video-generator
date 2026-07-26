from __future__ import annotations

import asyncio
import base64
import os
import uuid

import fal_client

from app.config import settings


class FalProvider:
    """Generates video clips via fal.ai queue API."""

    async def generate_clip(
        self,
        image_path: str,
        prompt: str,
        duration_s: int,
        aspect_ratio: str,
        model: str,
    ) -> str:
        endpoint = settings.model_endpoints.get(model)
        if not endpoint:
            raise ValueError(f"Unknown model: {model}")

        # Read image and encode as data URI
        with open(image_path, "rb") as f:
            img_bytes = f.read()

        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        if ext == "jpg":
            ext = "jpeg"
        data_uri = f"data:image/{ext};base64,{base64.b64encode(img_bytes).decode()}"

        arguments = {
            "image_url": data_uri,
            "prompt": prompt,
            "duration": str(duration_s) if model != "wan" else "5",
            "aspect_ratio": aspect_ratio,
        }

        os.environ["FAL_KEY"] = settings.fal_key

        def on_queue_update(update):
            if isinstance(update, fal_client.InProgress) and hasattr(update, "logs"):
                for log in update.logs:
                    print(f"  [fal] {log.get('message', log)}")

        result = await fal_client.subscribe_async(
            endpoint,
            arguments=arguments,
            with_logs=True,
            on_queue_update=on_queue_update,
        )

        # Download the video
        video_url = result["video"]["url"]
        out_dir = os.path.join(settings.data_dir, "clips")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{uuid.uuid4().hex}.mp4")

        import urllib.request
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, urllib.request.urlretrieve, video_url, out_path)

        return out_path
