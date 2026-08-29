from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable

from app.config import settings
from app.providers.mock_provider import MockProvider
from app.providers.fal_provider import FalProvider
from app.services.ffmpeg import extract_last_frame
from app.services.job_state import clip_path_for, clips_dir

SceneDone = Callable[[int, str], Awaitable[None]]
SceneError = Callable[[int, str], Awaitable[None]]


def _timeout_for(scene: dict) -> float:
    return settings.clip_timeout_s


def _get_provider():
    if settings.mock_provider:
        return MockProvider()
    return FalProvider()


async def generate_clips(
    state: dict,
    on_scene_done: SceneDone | None = None,
    on_scene_error: SceneError | None = None,
) -> dict:
    """Generate video clips for all scenes, with last-frame chaining.

    Resumable: a scene whose clip is already on disk is skipped, so restarting a
    half-finished job does not pay fal.ai twice for the same seconds of video.

    The callbacks are parameters rather than entries in `state` so that `state`
    stays a serializable snapshot.
    """
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
    os.makedirs(clips_dir(job_id), exist_ok=True)

    # Group scenes into chains: sequences where chain_from_prev=True
    chains: list[list[int]] = []
    for i, scene in enumerate(scenes):
        if not scene.get("chain_from_prev", False) or i == 0:
            chains.append([i])
        else:
            chains[-1].append(i)

    def already_rendered(idx: int) -> bool:
        """True when scene `idx` needs no provider call.

        Called before the semaphore is taken, so skipped scenes do not consume
        a concurrency slot.
        """
        scene = scenes[idx]
        out_path = clip_path_for(job_id, idx)

        if scene.get("status") != "done":
            return False
        # The disk gets the final word. `scene["clip_path"]` is deliberately not
        # consulted: older rows point at a uuid name that stitch would never find.
        if not os.path.exists(out_path):
            return False
        # A crashed download or a killed ffmpeg leaves a 0-byte file behind.
        return os.path.getsize(out_path) > 0

    async def generate_single(idx: int, input_image: str) -> str:
        scene = scenes[idx]
        out_path = clip_path_for(job_id, idx)

        if already_rendered(idx):
            print(f"[job {job_id}] scene {idx}: reusing existing clip")
            clip_paths[idx] = out_path
            return out_path

        async with semaphore:
            timeout = _timeout_for(scene)
            for attempt in range(settings.clip_max_retries + 1):
                try:
                    # Cancelling this cancels the await, not a thread already
                    # running inside it: FalProvider downloads via
                    # run_in_executor, so a timed-out download keeps going and
                    # leaves an orphan {out_path}.part behind (Faza 1.3 sweeps it).
                    clip_path = await asyncio.wait_for(
                        provider.generate_clip(
                            image_path=input_image,
                            prompt=scene["sub_prompt"],
                            duration_s=scene["duration_s"],
                            aspect_ratio=aspect_ratio,
                            model=model,
                            out_path=out_path,
                        ),
                        timeout=timeout,
                    )
                    clip_paths[idx] = clip_path
                    # Keep the dict in step with the DB for any caller reusing it.
                    scene["clip_path"] = clip_path
                    scene["status"] = "done"
                    if on_scene_done:
                        await on_scene_done(idx, clip_path)
                    return clip_path
                except Exception as e:
                    # TimeoutError stringifies to '', so naming it is the only
                    # way the message says anything at all.
                    reason = (
                        f"przekroczono limit {timeout:g}s"
                        if isinstance(e, asyncio.TimeoutError)
                        else f"{type(e).__name__}: {e}"
                    )
                    if attempt == settings.clip_max_retries:
                        msg = (
                            f"Scena {idx + 1} nie powiodła się po "
                            f"{settings.clip_max_retries + 1} próbach: {reason}"
                        )
                        scene["status"] = "error"
                        if on_scene_error:
                            await on_scene_error(idx, msg)
                        raise RuntimeError(msg)

    async def process_chain(chain_indices: list[int]):
        """Process a chain of scenes sequentially (for last-frame chaining)."""
        for i, idx in enumerate(chain_indices):
            scene = scenes[idx]
            if i == 0:
                input_image = image_paths[scene["image_index"]]
            else:
                # Extract last frame from previous clip — works for a skipped
                # scene too, its clip file is on disk either way.
                prev_clip = clip_paths[chain_indices[i - 1]]
                frame_path = os.path.join(frames_dir, f"frame_{chain_indices[i-1]}.png")
                await extract_last_frame(prev_clip, frame_path)
                input_image = frame_path

            await generate_single(idx, input_image)

    # Process chains in parallel, scenes within a chain sequentially.
    tasks = [process_chain(chain) for chain in chains]
    # return_exceptions: one failing chain must not cut short the others. Every
    # clip that does render is one the user will not pay for again on retry.
    await asyncio.gather(*tasks, return_exceptions=True)

    # Scenes that raised. Note this is not the same as "not done": a scene after
    # a failure in the same chain never ran at all and is still 'pending'.
    failed = [i for i, s in enumerate(scenes) if s.get("status") == "error"]

    if failed:
        print(f"[job {job_id}] {len(failed)}/{len(scenes)} scen nieudanych: {failed}")

    return {
        # Gaps are squeezed out, so this list only lines up with chain_flags when
        # every scene rendered — run_generation checks that before stitching.
        "clip_paths": [p for p in clip_paths if p is not None],
        "status": "error" if failed else "stitching",
        "failed_scenes": failed,
    }
