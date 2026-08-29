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

    # --- Auth / mail (Faza 2) ---
    mail_provider: str = field(default_factory=lambda: os.getenv("MAIL_PROVIDER", "console"))
    resend_api_key: str = field(default_factory=lambda: os.getenv("RESEND_API_KEY", ""))
    mail_from: str = field(default_factory=lambda: os.getenv("MAIL_FROM", "Video Generator <onboarding@resend.dev>"))
    base_url: str = field(default_factory=lambda: os.getenv("BASE_URL", "http://localhost:8000"))
    session_secret: str = field(default_factory=lambda: os.getenv("SESSION_SECRET", ""))
    cookie_secure: bool = field(default_factory=lambda: os.getenv("COOKIE_SECURE", "false").lower() in ("true", "1", "yes"))

    session_cookie_name: str = "vg_session"
    session_ttl_days: int = 30
    magic_link_ttl_min: int = 15
    login_rate_per_email: int = 3
    login_rate_per_ip: int = 10
    login_rate_window_s: int = 900

    max_upload_mb: int = 10
    max_images: int = 30
    # Ile lancuchow scen leci rownolegle. Env-driven, bo to jedyny parametr,
    # ktory trzeba zejsc na maszynie o 1 vCPU / 1 GB RAM: kazdy rownolegly
    # lancuch to wlasny proces ffmpeg przy normalizacji klipu.
    semaphore_limit: int = field(default_factory=lambda: int(os.getenv("SEMAPHORE_LIMIT", "4")))
    clip_poll_interval: float = 5.0
    clip_max_retries: int = 2
    # Ceiling for one provider call. Without it a provider that never answers
    # parks the job forever: no error, no 'interrupted', no way out.
    clip_timeout_s: float = field(default_factory=lambda: float(os.getenv("CLIP_TIMEOUT_S", "600")))
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
        "wan": "fal-ai/wan-25-preview/image-to-video",
        "kling-2.5-turbo": "fal-ai/kling-video/v2.5-turbo/pro/image-to-video",
        "veo-3.1-fast": "fal-ai/veo3.1/fast/image-to-video",
    })

    # Per-model request schema. Every fal.ai endpoint takes `image_url` and
    # `prompt`, and nothing else is shared: sending a field the endpoint does
    # not declare is a 422, so each model has to be described rather than
    # assumed. Values below mirror the published OpenAPI schema of each
    # endpoint -- re-check them when bumping an endpoint slug.
    #
    #   durations       (seconds, api_value) pairs the endpoint accepts, sorted
    #                   ascending. Scene durations are planned as 5 or 10s, so a
    #                   model on a different grid needs mapping, not passthrough.
    #   aspect_ratio    whether the endpoint declares an `aspect_ratio` field.
    #   extra           constant arguments always sent for this model.
    model_params: dict[str, dict] = field(default_factory=lambda: {
        "wan": {
            "durations": [(5, "5"), (10, "10")],
            "aspect_ratio": False,
            "extra": {"resolution": "720p"},
        },
        "kling-2.5-turbo": {
            "durations": [(5, "5"), (10, "10")],
            "aspect_ratio": False,
            "extra": {},
        },
        "veo-3.1-fast": {
            "durations": [(4, "4s"), (6, "6s"), (8, "8s")],
            "aspect_ratio": True,
            "extra": {"resolution": "720p"},
        },
    })


settings = Settings()
