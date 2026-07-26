from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScenePlan:
    image_index: int
    sub_prompt: str
    duration_s: int
    chain_from_prev: bool


@dataclass
class PipelineState:
    job_id: str = ""
    prompt: str = ""
    model: str = "wan"
    aspect_ratio: str = "16:9"
    target_duration_s: int = 120
    image_paths: list[str] = field(default_factory=list)
    scenes: list[ScenePlan] = field(default_factory=list)
    est_cost_usd: float = 0.0
    clip_paths: list[str] = field(default_factory=list)
    chain_flags: list[bool] = field(default_factory=list)
    final_path: str = ""
    error: str = ""
    status: str = "uploaded"
    # Control flow
    approved: bool = False
