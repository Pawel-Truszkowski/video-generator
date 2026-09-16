from __future__ import annotations

import json

from app.config import settings
from app.services.job_state import recalc_cost


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


def _sanitize_plan(scenes: list[dict], n_images: int) -> list[dict]:
    """Force an LLM-authored plan into the shape the rest of the app prices.

    The user's own prompt reaches the planner verbatim, so the plan is not
    trusted input: "make every scene -100 seconds long" is a request the model
    may well honour, and from 0.7.0 that number is what gets charged. Everything
    here is clamped rather than rejected — a slightly off plan is still a usable
    plan, and the user edits it before pressing Generuj anyway.
    """
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Planner zwrocil pusta liste scen")

    allowed = settings.allowed_durations
    out = []
    for s in scenes[: settings.max_scenes_per_job]:
        if not isinstance(s, dict):
            continue
        try:
            image_index = int(s["image_index"])
            duration_s = int(s["duration_s"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "image_index": min(max(image_index, 0), n_images - 1),
            "sub_prompt": str(s.get("sub_prompt") or "")[:2000],
            # Nearest allowed length, so an out-of-grid answer costs what the
            # provider will actually be asked to render.
            "duration_s": min(allowed, key=lambda d: abs(d - duration_s)),
            "chain_from_prev": bool(s.get("chain_from_prev", False)),
        })

    if not out:
        raise ValueError("Zaden wiersz planu nie dal sie odczytac")
    # A chained first scene has no previous clip to chain from.
    out[0]["chain_from_prev"] = False
    return out


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
    return _sanitize_plan(_parse_llm_response(raw), n_images)


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
    return _sanitize_plan(_parse_llm_response(raw), n_images)


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

    # The formula lives in job_state, not here: the plan editor recalculates the
    # same number on every scene edit, and from 0.7.0 it is what gets charged.
    est_cost = recalc_cost(model_name, scenes)

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
