# Video Generator

[🇵🇱 Polski](README.md) | 🇬🇧 English

A web application that turns a set of photos and a text description into a
finished MP4 video. Each scene is rendered as an *image-to-video* clip by models
hosted on [fal.ai](https://fal.ai) (Wan, Kling, Veo), and the clips are stitched
together with FFmpeg into a single file with smooth transitions.

The project is a working proof of concept evolving towards an MVP: it has user
accounts, a credit system with Stripe payments, an admin panel, resumable
generation after interruptions and automatic data cleanup. Current version:
**0.8.0** — release history in [`docs/changelog.md`](docs/changelog.md)
(in Polish).

---

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Video models and pricing](#video-models-and-pricing)
- [API](#api)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Production deployment](#production-deployment)
- [Verifying changes](#verifying-changes)
- [Limitations and roadmap](#limitations-and-roadmap)
- [Documentation](#documentation)

---

## Features

- **Two scene-planning modes**
  - *manual* (default) — one scene card per photo: motion description, duration,
    chaining to the previous scene; an optional style appended to every scene,
  - *AI* — an LLM splits the film description into scenes, and the user reviews
    and edits the plan before generation.
- **Last-frame chaining** — the last frame of clip N becomes the input image of
  clip N+1, preserving motion continuity between scenes.
- **Real-time progress** via Server-Sent Events.
- **Failure resilience** — job state stored in SQLite, resuming after a container
  restart without paying again for finished clips, per-scene retry, timeouts and
  readable provider errors.
- **Passwordless login** — magic link sent by e-mail (Resend), session kept in an
  HMAC-signed `httpOnly` cookie.
- **Credit system** — reservation before generation, automatic refund for scenes
  that were not produced, top-ups through Stripe Checkout, welcome credits.
- **Admin panel** — job and user lists, manual top-ups, account blocking.
- **Data retention** — working files are removed after `RETENTION_DAYS`; finished
  videos stay until the user deletes them.
- **Mock mode** — the whole flow runs locally with no API keys and no cost.

## How it works

```
 photos + description
       │
       ▼
┌─────────────┐   ┌──────────────┐   ┌────────────────┐   ┌──────────────┐
│  validate   │──►│ plan_scenes  │──►│ generate_clips │──►│    stitch    │──► final.mp4
│ Pillow,     │   │ manual or    │   │ fal.ai i2v,    │   │ FFmpeg:      │
│ 1280×720    │   │ LLM          │   │ scene chains   │   │ xfade/concat │
└─────────────┘   └──────┬───────┘   └────────────────┘   └──────────────┘
                         │
                 user reviews / edits
                 the plan
```

1. **Upload** — the user uploads photos (up to 30, max. 10 MB each) and picks a
   model. Images are converted to RGB and letterboxed to 1280×720.
2. **Plan** — in manual mode the plan arrives together with the upload; in AI
   mode the planner (OpenAI `gpt-4o-mini` → Anthropic Claude Sonnet 4 →
   algorithmic fallback) produces the scene list. LLM plans are sanitized
   (allowed durations, image index range), because they directly affect the
   price.
3. **Generation** — scenes are grouped into chains. Chains run in parallel
   (`SEMAPHORE_LIMIT`), scenes within a chain run sequentially, since each needs
   the previous clip's last frame. Every clip is persisted as soon as it is
   rendered — a failure in scene 5 does not lose scenes 0–4.
4. **Stitching** — clips are normalized to a common fps/resolution/codec and
   joined: a crossfade between independent scenes, a hard cut within a chain.
5. **Settlement** — when the job ends (also on error or container shutdown) it is
   settled, and credits for scenes that were not rendered go back to the account.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn, `sse-starlette` |
| Database | SQLite (`aiosqlite`) |
| Video generation | fal.ai queue API (`fal-client`) |
| Scene planning | OpenAI `gpt-4o-mini`, Anthropic Claude Sonnet 4 |
| Media processing | FFmpeg, Pillow |
| E-mail | Resend (HTTP API) |
| Payments | Stripe Checkout + webhooks (HTTP API, no SDK) |
| Frontend | HTML, CSS, vanilla JavaScript — no framework, no build step |
| Runtime | Docker, Docker Compose |

## Quick start

**Requirements:** Docker with Docker Compose v2. No API keys are needed to run
in mock mode.

```bash
git clone <repository-url>
cd video-generator
cp .env.example .env          # MOCK_PROVIDER=true by default — no cost
docker compose up --build
```

1. Open <http://localhost:8000>.
2. Enter any e-mail address. With `MAIL_PROVIDER=console` the login link is
   printed to the container logs (and returned in the HTTP response) — open it
   in the browser.
3. Upload a few photos, describe the scenes and click **Generuj** (Generate).
   In mock mode each clip is a still photo with the prompt text burned in,
   rendered locally by FFmpeg.

> **Note for developers:** `./app` is bind-mounted into the container, but
> Uvicorn runs without `--reload`. Python changes require
> `docker compose restart app`; files in `app/static/` are served live (a hard
> browser refresh is enough).

> The user interface is currently in Polish only.

### Using real models

Set `MOCK_PROVIDER=false` in `.env`, along with `FAL_KEY` and at least one of
`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`. From then on every generation is a
**paid** fal.ai call — see the pricing below.

## Configuration

All configuration comes from environment variables, read once at startup into
an immutable `settings` object (`app/config.py`). Full, commented templates:
[`.env.example`](.env.example) (local) and [`.env.prod.example`](.env.prod.example)
(production).

### AI providers

| Variable | Default | Description |
|---|---|---|
| `MOCK_PROVIDER` | `true`¹ | `true` — local planner and FFmpeg clips, zero cost; `false` — real APIs |
| `FAL_KEY` | — | fal.ai key (video generation) |
| `OPENAI_API_KEY` | — | OpenAI key (scene planner, first choice) |
| `ANTHROPIC_API_KEY` | — | Anthropic key (scene planner, fallback) |
| `CLIP_TIMEOUT_S` | `600` | Time limit for generating a single clip |
| `SEMAPHORE_LIMIT` | `4` | Number of scene chains generated in parallel (use `1` on 1 vCPU) |

¹ Value from `.env.example`; if the variable is unset, the application assumes `false`.

### Authentication and e-mail

| Variable | Default | Description |
|---|---|---|
| `MAIL_PROVIDER` | `console` | `console` — link in logs and HTTP response (dev only); `resend` — real e-mail |
| `RESEND_API_KEY` | — | Required with `MAIL_PROVIDER=resend` |
| `MAIL_FROM` | `onboarding@resend.dev` | Sender; in production it must be in a verified domain |
| `BASE_URL` | `http://localhost:8000` | Address used to build the login link |
| `SESSION_SECRET` | — | Session HMAC secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `COOKIE_SECURE` | `false` | `true` only behind HTTPS |
| `ADMIN_EMAILS` | — | Comma-separated administrator addresses |

### Credits and payments

| Variable | Default | Description |
|---|---|---|
| `CREDIT_MARGIN` | `2.0` | Multiplier between the provider's cost and the user's price |
| `WELCOME_CREDITS` | `500` | Starting credits for a new account (`0` disables) |
| `STRIPE_ENABLED` | `false` | With `false`, only an administrator can top up accounts |
| `STRIPE_SECRET_KEY` | — | Stripe API key |
| `STRIPE_WEBHOOK_SECRET` | — | Webhook signing secret (different from the API key) |

### Limits and retention

| Variable | Default | Description |
|---|---|---|
| `MAX_ACTIVE_JOBS_PER_USER` | `3` | How many of one user's jobs may run at the same time |
| `RETENTION_DAYS` | `7` | After how many days working files (uploads, frames, clips) are removed |
| `CLEANUP_INTERVAL_H` | `24` | Cleanup loop interval (the first sweep always runs at startup) |

### Configuration safeguards

The application **refuses to start** with unsafe combinations of settings:

- `COOKIE_SECURE=true` without `SESSION_SECRET`,
- `COOKIE_SECURE=true` with `MAIL_PROVIDER=console` — console mode returns the
  login link in the HTTP response, which allows logging into any account,
- `MAIL_PROVIDER=resend` without `RESEND_API_KEY` or with the shared
  `@resend.dev` sender.

## Video models and pricing

1 credit = 1 US cent. The credit rate is **derived** from the fal.ai cost and
`CREDIT_MARGIN` — there is no second price table that could drift from the
provider's pricing.

| Model | fal.ai endpoint | fal.ai cost | Credits / s (margin 2.0) | 3-min video (fal.ai cost) |
|---|---|---|---|---|
| Wan 2.5 | `fal-ai/wan-25-preview/image-to-video` | $0.05/s | 10 | ~$9 |
| Kling 2.5 Turbo Pro | `fal-ai/kling-video/v2.5-turbo/pro/image-to-video` | $0.07/s | 14 | ~$13 |
| Veo 3.1 Fast | `fal-ai/veo3.1/fast/image-to-video` | $0.10/s | 20 | ~$18 |

Scenes are 5 or 10 seconds long. Veo only accepts 4/6/8 s, so the duration is
mapped to the nearest allowed value (never longer than planned).

Top-up packages (Stripe): **Starter** $5 → 500 credits, **Standard** $15 → 1650
credits, **Pro** $50 → 6000 credits.

## API

Interactive OpenAPI documentation is available at <http://localhost:8000/docs>.

All endpoints except `/auth/*`, `/health` and the Stripe webhook require a
session. Job-scoped endpoints check ownership and return **404** (not 403) for
someone else's `job_id`, so identifiers cannot be enumerated.

<details>
<summary><strong>Authentication</strong></summary>

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/request-login` | Sends a login link to the given e-mail (rate-limited) |
| `GET` | `/auth/callback?token=…` | Consumes the token, sets the session cookie, redirects to `/` |
| `GET` | `/auth/me` | Current user |
| `POST` | `/auth/logout` | Ends the session |

</details>

<details>
<summary><strong>Jobs and generation</strong></summary>

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs` | Upload photos + model + plan mode (`manual` with a scene list, or `auto`) |
| `GET` | `/jobs` | The user's videos |
| `GET` | `/jobs/{id}` | Job status and scene list |
| `DELETE` | `/jobs/{id}` | Deletes the job together with its files and finished video |
| `POST` | `/jobs/{id}/plan` | Runs AI planning (`auto` mode only) |
| `POST` | `/jobs/{id}/scenes/sync` | Replaces the scene list (add, remove, reorder, chains) |
| `POST` | `/jobs/{id}/scenes/{idx}/update` | Edits a single scene |
| `POST` | `/jobs/{id}/generate` | Accepts the plan, reserves credits and starts generation |
| `POST` | `/jobs/{id}/scenes/{idx}/retry` | Retries generation of a single scene |
| `POST` | `/jobs/{id}/resume` | Resumes an interrupted job from the right stage |
| `GET` | `/jobs/{id}/events` | SSE progress stream |
| `GET` | `/jobs/{id}/thumbnail` | Job thumbnail |
| `GET` | `/jobs/{id}/images/{index}` | Uploaded image at the given index |
| `GET` | `/media/{id}/final.mp4` | Download the finished video |

</details>

<details>
<summary><strong>Credits</strong></summary>

| Method | Path | Description |
|---|---|---|
| `GET` | `/credits` | Balance and transaction history |
| `GET` | `/credits/packages` | Available top-up packages |
| `POST` | `/credits/checkout` | Creates a Stripe Checkout session |
| `POST` | `/credits/webhook` | Stripe webhook (authenticated by HMAC signature, no session) |

</details>

<details>
<summary><strong>Administration</strong> (requires an account listed in <code>ADMIN_EMAILS</code>)</summary>

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/jobs` | All jobs |
| `DELETE` | `/admin/jobs/{id}` | Delete any job |
| `GET` | `/admin/users` | Users with balances |
| `POST` | `/admin/users/{id}/credits` | Manual top-up or balance correction |
| `POST` | `/admin/users/{id}/active` | Block / unblock an account |

</details>

<details>
<summary><strong>Monitoring</strong></summary>

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Database status and free disk space; `503` on failure |

</details>

## Architecture

The application is a **single container**: FastAPI with a single Uvicorn worker,
SQLite as the only source of truth and background tasks started with
`asyncio.create_task`. This setup is deliberately sized for a small VPS
(1 vCPU, 1 GB RAM); moving to a task queue, S3 and PostgreSQL is described in
the roadmap as the scaling phase.

Key design decisions:

- **Job state lives only in SQLite.** There is no in-memory registry — every
  background task rebuilds its state from the database
  (`app/services/job_state.py`). After a restart, jobs that were in progress are
  marked `interrupted` and can be resumed.
- **A scene is done when its file exists on disk**, not merely when the database
  says so. The clip path is a pure function of `(job_id, idx)`, which makes a
  retry idempotent and prevents paying twice for the same clip.
- **The credit balance is changed by an atomic `UPDATE … WHERE balance >= ?`**,
  and the `credits` table is an append-only audit log. A job's settlement is
  derived from the ledger rows carrying its `job_id`, is idempotent and runs in
  `finally`, so it also works when the container shuts down.
- **Provider abstraction** — the `VideoProvider` protocol
  (`app/providers/base.py`) with `FalProvider` and `MockProvider`
  implementations. A new model is an entry in `model_endpoints` /
  `model_costs` / `model_params`; a new vendor is a new class satisfying the
  protocol.
- **Session in a cookie, not an `Authorization` header** — the frontend uses
  `EventSource`, which cannot set headers.

> The `app/graph/` directory is named after the early LangGraph-based prototype.
> Today the pipeline is a sequence of plain async functions — LangGraph is not a
> project dependency.

## Project structure

```
.
├── app/
│   ├── main.py                 # FastAPI app, lifespan, /health, static mount
│   ├── config.py               # env settings, models, pricing, packages
│   ├── db.py                   # SQLite schema and migrations
│   ├── api/
│   │   ├── auth.py             # magic link, sessions, rate limiting
│   │   ├── jobs.py             # jobs, scenes, generation, resume, SSE, media
│   │   ├── credits.py          # balance, packages, Stripe Checkout and webhook
│   │   └── admin.py            # admin panel
│   ├── auth/                   # HMAC tokens, users, dependencies (require_user, get_owned_job)
│   ├── mail/                   # login link delivery: console (dev) and Resend (prod)
│   ├── graph/
│   │   ├── pipeline.py         # stage orchestration and SSE events
│   │   └── nodes/              # validate, plan_scenes, generate_clips, stitch
│   ├── providers/              # VideoProvider: fal.ai and mock
│   ├── services/
│   │   ├── job_state.py        # DB ⇄ pipeline state boundary
│   │   ├── credits.py          # the only module writing balance and ledger
│   │   ├── stripe_client.py    # Stripe client on httpx
│   │   ├── cleanup.py          # retention and file deletion
│   │   └── ffmpeg.py           # normalization, concat, crossfade, frame extraction
│   └── static/                 # frontend: index.html, app.js, style.css
├── docs/
│   ├── changelog.md            # release history with design rationale
│   ├── roadmap.md              # MVP development plan
│   └── deployment.md           # VPS deployment guide
├── scripts/backup-db.sh        # SQLite backup (for cron)
├── docker-compose.yml          # development environment
├── docker-compose.prod.yml     # production environment
├── Dockerfile
└── requirements.txt
```

Runtime data goes to the `./data` volume (mounted as `/data`): the
`video_gen.db` database, the `uploads/`, `frames/` and `clips/` directories
(working files, subject to retention) and `final/` (finished videos).

## Production deployment

Production uses a separate Compose file:

```bash
cp .env.prod.example .env      # fill in the secrets
docker compose -f docker-compose.prod.yml up -d --build
```

Differences from the development environment:

- the code is baked into the image (no `./app` bind mount) — an update requires
  `git pull` **and** `--build`,
- `restart: unless-stopped`, a healthcheck on `/health`, log rotation and a
  container memory limit,
- Uvicorn with `--proxy-headers` — TLS is terminated by a reverse proxy in front
  of the application.

Minimum production requirements: HTTPS, `COOKIE_SECURE=true`,
`MAIL_PROVIDER=resend` with a sender in a verified domain, a random
`SESSION_SECRET`. On a 1 vCPU server set `SEMAPHORE_LIMIT=1`.

Database backup (consistent, via `sqlite3.backup()`, 7-day rotation):

```bash
0 3 * * * cd /opt/video-generator && ./scripts/backup-db.sh >> /var/log/vg-backup.log 2>&1
```

Step-by-step guide: [`docs/deployment.md`](docs/deployment.md) (in Polish).

## Verifying changes

The project has no automated test suite. Changes are verified manually, in mock
mode:

1. `docker compose up --build`, log in with the link from the logs.
2. Walk through the full flow: upload → plan → generation → video download.
3. Pipeline stage errors are logged with a stack trace in the container logs.
4. **Resume:** restart the container during generation
   (`docker compose restart app`) and click *Wznów* (Resume) in "Moje filmy"
   (My videos).
5. **Retention:** set `RETENTION_DAYS=0` and restart the container — cleanup
   runs at startup.

## Limitations and roadmap

Known limitations of the current version:

- single process and SQLite — no horizontal scaling,
- files on local disk (no S3/CDN),
- output limited to 1280×720, 16:9, no audio track,
- no e-mail notifications when generation finishes,
- user interface in Polish only,
- no automated tests.

The development plan (stability → accounts → payments → deployment → UX →
scaling) and the list of known issues from code review:
[`docs/roadmap.md`](docs/roadmap.md) (in Polish).

## Documentation

Apart from `CLAUDE.md`, the project documents are written in Polish.

| Document | Contents |
|---|---|
| [`docs/changelog.md`](docs/changelog.md) | Release history with the rationale behind key decisions |
| [`docs/roadmap.md`](docs/roadmap.md) | Development phases, task status, known issues |
| [`docs/deployment.md`](docs/deployment.md) | VPS deployment, HTTPS, memory limits, switching to fal.ai |
| [`CLAUDE.md`](CLAUDE.md) | Detailed architecture rules and code invariants (in English) |
| [`plan-implementacji.md`](plan-implementacji.md) | Original PoC plan (historical) |
