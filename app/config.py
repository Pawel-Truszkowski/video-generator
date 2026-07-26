from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    fal_key: str = field(default_factory=lambda: os.getenv("FAL_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    mock_provider: bool = field(default_factory=lambda: os.getenv("MOCK_PROVIDER", "false").lower() in ("true", "1", "yes"))
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", "/data"))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "/data/video_gen.db"))
    max_upload_mb: int = 10
    max_images: int = 30
    semaphore_limit: int = 4
    clip_poll_interval: float = 5.0
    clip_max_retries: int = 2
    crossfade_duration: float = 0.5
    output_width: int = 1280
    output_height: int = 720
    output_fps: int = 24

    # Cost per second of video by model
    model_costs: dict[str, float] = field(default_factory=lambda: {
        "wan": 0.05,
        "kling-2.5-turbo": 0.07,
        "veo-3.1-fast": 0.10,
    })

    model_endpoints: dict[str, str] = field(default_factory=lambda: {
        "wan": "fal-ai/wan/image-to-video",
        "kling-2.5-turbo": "fal-ai/kling-video/v2.5-turbo/pro/image-to-video",
        "veo-3.1-fast": "fal-ai/veo3.1/fast/image-to-video",
    })


settings = Settings()
