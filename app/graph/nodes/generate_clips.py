from __future__ import annotations

import asyncio
import os
import uuid

from app.config import settings
from app.providers.mock_provider import MockProvider
from app.providers.fal_provider import FalProvider
from app.services.ffmpeg import extract_last_frame


def _get_provider():
    if settings.mock_provider:
        return MockProvider()
    return FalProvider()


async def generate_clips(state: dict) -> dict:
    """Generate video clips for all scenes, with last-frame chaining."""
    scenes = state["scenes"]
    image_paths = state["image_paths"]
    model = state["model"]
    aspect_ratio = state["aspect_ratio"]
    job_id = state["job_id"]

    provider = _get_provider()
    semaphore = asyncio.Semaphore(settings.semaphore_limit)

    clip_paths: list[str | None] = [None] * len(scenes)
    frames_dir = os.path.join(settings.data_dir, "frames", job_id)
    os.makedirs(frames_dir, exist_ok=True)

    # Group scenes into chains: sequences where chain_from_prev=True
    chains: list[list[int]] = []
    for i, scene in enumerate(scenes):
        if not scene.get("chain_from_prev", False) or i == 0:
            chains.append([i])
        else:
            chains[-1].append(i)

    async def generate_single(idx: int, input_image: str) -> str:
        scene = scenes[idx]
        async with semaphore:
            for attempt in range(settings.clip_max_retries + 1):
                try:
                    clip_path = await provider.generate_clip(
                        image_path=input_image,
                        prompt=scene["sub_prompt"],
                        duration_s=scene["duration_s"],
                        aspect_ratio=aspect_ratio,
                        model=model,
                    )
                    clip_paths[idx] = clip_path
                    return clip_path
                except Exception as e:
                    if attempt == settings.clip_max_retries:
                        raise RuntimeError(f"Scene {idx} failed after {settings.clip_max_retries + 1} attempts: {e}")

    async def process_chain(chain_indices: list[int]):
        """Process a chain of scenes sequentially (for last-frame chaining)."""
        for i, idx in enumerate(chain_indices):
            scene = scenes[idx]
            if i == 0:
                input_image = image_paths[scene["image_index"]]
            else:
                # Extract last frame from previous clip
                prev_clip = clip_paths[chain_indices[i - 1]]
                frame_path = os.path.join(frames_dir, f"frame_{chain_indices[i-1]}.png")
                await extract_last_frame(prev_clip, frame_path)
                input_image = frame_path

            await generate_single(idx, input_image)

    # Process chains in parallel, scenes within a chain sequentially
    tasks = [process_chain(chain) for chain in chains]
    await asyncio.gather(*tasks)

    return {
        "clip_paths": [p for p in clip_paths if p is not None],
        "status": "stitching",
    }
