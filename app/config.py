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
    # Admin panel (Faza 2.3). Lowercase because that is how normalize_email()
    # stores users.email: the Python-side comparison in get_or_create_user does
    # not know about the column's COLLATE NOCASE.
    admin_emails: frozenset[str] = field(default_factory=lambda: frozenset(
        e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()
    ))

    session_cookie_name: str = "vg_session"
    session_ttl_days: int = 30
    magic_link_ttl_min: int = 15
    login_rate_per_email: int = 3
    login_rate_per_ip: int = 10
    login_rate_window_s: int = 900

    max_upload_mb: int = 10
    max_images: int = 30

    # Allowed length of a single scene. Until 0.6.0 this was an LLM prompt
    # convention nobody enforced. Since 0.7.0 `duration_s` is the only dimension
    # of price, so a value off this list is a price nobody intended — and a
    # negative one is a reservation that *credits* the account.
    allowed_durations: tuple[int, ...] = (5, 10)
    # Upper bound on plan length. The LLM will not produce that many on its own;
    # this exists for /scenes/sync, which takes a scene list straight from the
    # client and prices it with no other limit in the way.
    max_scenes_per_job: int = 60

    # --- Data retention (Faza 1.3) ---
    # How many days after creation a job's working material (uploads, frames,
    # clips) is removed. The finished film in data/final is NOT swept — it is the
    # user's product and goes away only through DELETE /jobs/{id}.
    retention_days: int = field(default_factory=lambda: int(os.getenv("RETENTION_DAYS", "7")))
    # From env purely so this can be tested without waiting a day.
    cleanup_interval_h: float = field(default_factory=lambda: float(os.getenv("CLEANUP_INTERVAL_H", "24")))
    # How many of one user's jobs may run at once. On 1 vCPU each active job is
    # its own set of ffmpeg processes — and its own bill at the provider.
    max_active_jobs_per_user: int = field(default_factory=lambda: int(os.getenv("MAX_ACTIVE_JOBS_PER_USER", "3")))
    # How many scene chains run in parallel. Env-driven because it is the one
    # knob that has to come down on a 1 vCPU / 1 GB box: each parallel chain is
    # its own ffmpeg process during clip normalisation.
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

    # --- Credits and payments (Faza 3) ---
    # 1 credit = 1 US cent, integers only, so the ledger sums exactly.
    #
    # Multiplier between provider cost (model_costs, USD/s) and the user's price.
    # The credit rate is *derived* from it rather than listed separately: a second
    # rate table would drift from model_costs the first time fal.ai repriced.
    credit_margin: float = field(default_factory=lambda: float(os.getenv("CREDIT_MARGIN", "2.0")))
    # Welcome credits for a new account (500 = $5), granted idempotently — see
    # services/credits.sync_welcome_credits().
    welcome_credits: int = field(default_factory=lambda: int(os.getenv("WELCOME_CREDITS", "500")))

    # Stripe, off by default for the same reason MOCK_PROVIDER is on: local work
    # must not require an account at a payment provider. With stripe_enabled=False
    # /credits/checkout and /credits/webhook answer 503 and an admin grants
    # credits by hand.
    stripe_enabled: bool = field(default_factory=lambda: os.getenv("STRIPE_ENABLED", "false").lower() in ("true", "1", "yes"))
    stripe_secret_key: str = field(default_factory=lambda: os.getenv("STRIPE_SECRET_KEY", ""))
    stripe_webhook_secret: str = field(default_factory=lambda: os.getenv("STRIPE_WEBHOOK_SECRET", ""))
    # Clock-skew tolerance when verifying a webhook signature. Stripe suggests
    # 5 minutes; an older timestamp is a possible replay.
    stripe_webhook_tolerance_s: int = 300

    # Purchasable packages. `credits` is the source of truth for how much a buyer
    # gets: the webhook reads it from here by `id`, NEVER from the payload's
    # amount_total, which arrives from outside.
    credit_packages: tuple[dict, ...] = field(default_factory=lambda: (
        {"id": "starter", "label": "Starter", "price_usd": 5, "credits": 500},
        {"id": "standard", "label": "Standard", "price_usd": 15, "credits": 1650},
        {"id": "pro", "label": "Pro", "price_usd": 50, "credits": 6000},
    ))


settings = Settings()
