from __future__ import annotations

import json

from app.config import settings


SYSTEM_PROMPT = """You are a video production planner. Given a set of images and a description,
create a scene-by-scene plan for a video.

Rules:
- Each scene uses one image as the starting frame.
- Every uploaded image must be used at least once.
- duration_s must be either 5 or 10.
- Total duration should be close to the target (within 10s).
- chain_from_prev=true means this scene continues from the last frame of the previous clip
  (used for smooth motion continuity from the same image). The first scene is always false.
- sub_prompt describes the motion/action for this clip segment.

Return ONLY a JSON array (no markdown, no explanation):
[{"image_index": 0, "sub_prompt": "...", "duration_s": 5, "chain_from_prev": false}, ...]"""


def _mock_plan(n_images: int, target_s: int, prompt: str) -> list[dict]:
    """Generate a simple scene plan without LLM (for testing / when API key is unavailable)."""
    scenes = []
    remaining = target_s

    for img_i in range(n_images):
        dur = 10 if remaining >= 10 else 5
        scenes.append({
            "image_index": img_i,
            "sub_prompt": f"Slow camera pan across scene {img_i + 1}. {prompt[:80]}",
            "duration_s": dur,
            "chain_from_prev": False,
        })
        remaining -= dur

    img_cycle = 0
    while remaining >= 5:
        dur = 10 if remaining >= 10 else 5
        scenes.append({
            "image_index": img_cycle % n_images,
            "sub_prompt": f"Continue motion with gentle zoom on scene {img_cycle % n_images + 1}",
            "duration_s": dur,
            "chain_from_prev": True,
        })
        remaining -= dur
        img_cycle += 1

    return scenes


def _parse_llm_response(raw: str) -> list[dict]:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    return json.loads(raw)


async def _plan_with_openai(n_images: int, target: int, prompt: str) -> list[dict]:
    """Plan scenes using OpenAI API."""
    from openai import OpenAI

    user_msg = (
        f"Number of images: {n_images} (indices 0 to {n_images - 1})\n"
        f"Target video duration: {target} seconds\n"
        f"Description: {prompt}\n\n"
        f"Create the scene plan."
    )

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=4096,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )

    raw = response.choices[0].message.content
    return _parse_llm_response(raw)


async def _plan_with_anthropic(n_images: int, target: int, prompt: str) -> list[dict]:
    """Plan scenes using Anthropic API."""
    import anthropic

    user_msg = (
        f"Number of images: {n_images} (indices 0 to {n_images - 1})\n"
        f"Target video duration: {target} seconds\n"
        f"Description: {prompt}\n\n"
        f"Create the scene plan."
    )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = response.content[0].text
    return _parse_llm_response(raw)


async def plan_scenes(state: dict) -> dict:
    """Use LLM to create a scene plan from images and prompt."""
    prompt = state["prompt"]
    image_paths = state["image_paths"]
    target = state["target_duration_s"]
    model_name = state["model"]
    n_images = len(image_paths)

    scenes = None

    if not settings.mock_provider:
        # Try OpenAI first, then Anthropic, then mock
        if settings.openai_api_key:
            try:
                scenes = await _plan_with_openai(n_images, target, prompt)
            except Exception as e:
                print(f"OpenAI planning failed ({e}), trying next provider...")

        if scenes is None and settings.anthropic_api_key:
            try:
                scenes = await _plan_with_anthropic(n_images, target, prompt)
            except Exception as e:
                print(f"Anthropic planning failed ({e}), falling back to mock planner")

    if scenes is None:
        scenes = _mock_plan(n_images, target, prompt)

    # Calculate cost
    total_seconds = sum(s["duration_s"] for s in scenes)
    cost_per_sec = settings.model_costs.get(model_name, 0.05)
    est_cost = round(total_seconds * cost_per_sec, 2)

    scene_dicts = []
    chain_flags = []
    for s in scenes:
        scene_dicts.append({
            "image_index": s["image_index"],
            "sub_prompt": s["sub_prompt"],
            "duration_s": s["duration_s"],
            "chain_from_prev": s.get("chain_from_prev", False),
        })
        chain_flags.append(s.get("chain_from_prev", False))

    return {
        "scenes": scene_dicts,
        "chain_flags": chain_flags,
        "est_cost_usd": est_cost,
        "status": "planned",
    }
