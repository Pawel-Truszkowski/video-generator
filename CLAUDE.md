# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A working proof-of-concept (not the "VideoAI Studio" SaaS spec from the parent directory's docs) that turns a set of uploaded images + a text prompt into a stitched MP4. Single FastAPI container, SQLite, magic-link auth, no payments — see `docs/roadmap.md` for what's explicitly deferred (credits/Stripe, admin panel, S3 storage, Celery/Redis queue, disk cleanup) and `docs/changelog.md` for what each version shipped.

## Commands

```bash
cp .env.example .env            # fill in FAL_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY, or leave MOCK_PROVIDER=true
docker compose up --build       # serves on http://localhost:8000, live-reloads (./app is bind-mounted)
```

Production runs from a **second compose file**, `docker-compose.prod.yml` (`docker compose -f docker-compose.prod.yml up -d --build`): no `./app` bind-mount, so a `git pull` without `--build` changes nothing; plus `restart: unless-stopped`, a `/health` healthcheck, capped json-file logs and `mem_limit`. Deployment target and its constraints are in `docs/deployment.md`; `.env.prod.example` is the server-side env template.

Note the container does **not** hot-reload Python (uvicorn runs without `--reload`) — `./app` is bind-mounted, but you must `docker compose restart app` for backend changes. Static files under `app/static/` *are* served live; hard-reload the browser to bust its cache.

There is no local (non-Docker) run path documented and no test suite — `app.js`/pytest/etc. are not present. Validate changes by running the container and exercising the API/UI directly (see "Manual verification" below).

`MOCK_PROVIDER=true` (the `.env.example` default) replaces both the LLM scene planner and the fal.ai video calls with free local stand-ins (`_mock_plan` in `plan_scenes.py`, `MockProvider` in `mock_provider.py` — the latter burns a static image + drawn prompt text into an MP4 via ffmpeg). Flip to `false` only when you intend to spend real money on OpenAI/Anthropic/fal.ai calls.

### Manual verification

No automated tests exist. To check a change works: `docker compose up --build`, open `http://localhost:8000`, log in via the magic link printed to the logs, and drive the flow (upload → plan → generate) with `MOCK_PROVIDER=true` so it costs nothing. Watch container logs for the `traceback.print_exc()` output that `pipeline.py` emits on node failure.

To exercise resume, kill the container mid-generation (`docker compose restart app`) and press *Wznów* on the job in "Moje filmy" — the startup log line reports how many jobs were flagged `interrupted`.

## Auth (roadmap Faza 2, shipped in 0.2.0)

Passwordless magic link → HMAC-signed httpOnly session cookie. Three rules that are easy to break:

1. **`app/main.py` must include every router BEFORE the `StaticFiles` mount at `/`.** `Mount("/")` matches every path, so a router registered after it is silently unreachable — its routes return static-file 404s with no startup error.
2. **Never switch to `Authorization: Bearer`.** `app/static/app.js` consumes SSE via `new EventSource(...)`, whose constructor cannot set headers. The cookie is what makes `/jobs/{id}/events` work.
3. **New routes on the jobs router are protected automatically** — `router = APIRouter(dependencies=[Depends(require_user)])`. A *new router*, however, is not; add the dependency yourself. For anything scoped to one job, depend on `get_owned_job` (`app/auth/deps.py`) rather than re-querying — it is the single ownership code path and deliberately returns **404, not 403**, so job ids can't be enumerated.

`MAIL_PROVIDER=console` (the default) prints the login link to the container logs *and* returns it in the `POST /auth/request-login` response, so you can log in locally with no email setup. It is a full impersonation backdoor: it's double-gated on `not COOKIE_SECURE`, and the app refuses to start with `COOKIE_SECURE=true` + `console`.

Schema changes to existing tables go in `_migrate()` in `app/db.py`, not in the `SCHEMA` string — `CREATE TABLE IF NOT EXISTS` will not add columns to an existing DB, and `data/video_gen.db` is a real, populated file. Indexes referencing migrated columns must go in the separate `INDEXES` script that runs after `_migrate()`. Store timestamps as `strftime('%Y-%m-%dT%H:%M:%SZ','now')`; `datetime('now')` lacks a zone marker and the browser parses it as local time.

## Architecture

**The pipeline is NOT built on LangGraph** despite the `app/graph/` directory name (`langgraph`/`langgraph-checkpoint-sqlite` were dropped from `requirements.txt` in 0.4.1 — nothing imported them, and the install footprint mattered on a 1 GB VPS) — `app/graph/pipeline.py` just calls the four node functions directly as plain async functions with manual state-dict merging and SSE event emission. There's no graph and no checkpointing; resume exists but is hand-rolled against SQLite (see "Job state" below), not LangGraph's `interrupt()`. Don't assume LangGraph APIs are in play when reading `app/graph/`.

Request flow:

```
POST /jobs                 → save images to disk, INSERT jobs row (status=uploaded)
POST /jobs/{id}/plan       → asyncio.create_task(run_planning): validate → plan_scenes
                              (status becomes 'planned', pipeline pauses for user approval)
POST /jobs/{id}/generate   → asyncio.create_task(run_generation): generate_clips → stitch
POST /jobs/{id}/resume     → _decide_stage() picks the stage, then starts the matching task
GET  /jobs/{id}/events     → SSE fan-out from an in-memory asyncio.Queue per job_id
```

The pause between planning and generation is not a real interrupt — it's just that `/plan` and `/generate` are two separate endpoints/background tasks. Nothing is waiting during it: the planning task has already finished, and the `state` dict it worked on is gone.

### Job state (roadmap Faza 1.1, shipped in 0.3.0)

**SQLite is the only source of truth for a job.** There is no in-memory job registry — the `state` dict handed to the nodes is rebuilt per background task by `load_state()` in `app/services/job_state.py`, which is the single DB ⇄ pipeline-state boundary (`save_scenes`, `update_scene_fields`, `mark_scene_done`, `clip_path_for`, `reconcile_interrupted`). Nodes still take and return plain dicts; they just don't own anything that outlives their task.

Four rules that fall out of that, and are easy to break:

1. **A scene is "done" if its file is on disk, not if the DB says so.** `already_rendered()` (`generate_clips.py`) requires `status='done'` **and** `os.path.exists(clip_path_for(job_id, idx))` **and** a non-zero size. `clip_path_for` is a pure function of `(job_id, idx)` → `data/clips/{job_id}/scene_{idx:03d}.mp4`; the `scenes.clip_path` column is written but deliberately never read back, because pre-0.3.0 rows point at `uuid4()` names that `stitch` would never find. Providers therefore take `out_path` from the caller and must overwrite it, so a retry is idempotent.
2. **Persist per scene, not per node.** `generate_clips` reports each finished clip through the `on_scene_done` / `on_scene_error` callbacks; that's what makes a crash on scene 5 keep scenes 0-4 recorded (and unpaid-for a second time).
3. **`planning`/`generating`/`stitching` can only exist while a task is alive** (`STALE_STATUSES`). `reconcile_interrupted()` runs in `lifespan` and flips any it finds to `interrupted` — safe only because this is a single uvicorn worker in a single container. `_running: set[str]` in `app/api/jobs.py` guards double-starts; losing it on restart is correct, not a bug.
4. **A `planned` job never resumes into generation by itself.** `_decide_stage()` returns `planning`/`planned`/`generating`/`done`; only the first and third start a task, because pressing *Generuj* is what spends money. A job that failed on one scene resumes as `generating`, not `planning` — replanning calls `save_scenes`, which is DELETE+INSERT and would drop paid-for clips. For the same reason plan edits are rejected with 409 once any scene has `status='done'`, not just on job status.

### Pipeline stages (`app/graph/nodes/`)

1. **validate** — format/size checks, Pillow RGB convert + letterbox-resize to `settings.output_width/height` (1280×720), writes `*_processed.jpg` next to originals.
2. **plan_scenes** — LLM call (OpenAI gpt-4o-mini first, Anthropic Claude Sonnet 4 fallback, algorithmic `_mock_plan` last resort) turns the prompt + image count into a JSON scene list (`image_index`, `sub_prompt`, `duration_s` ∈ {5,10}, `chain_from_prev`). Also computes `est_cost_usd` from `settings.model_costs`.
3. **generate_clips** — groups scenes into chains on `chain_from_prev`; chains run in parallel (`asyncio.Semaphore(settings.semaphore_limit)`, default 4), scenes within a chain run sequentially because each needs the previous clip's last frame (`extract_last_frame`, ffmpeg) as its input image. Retries each scene up to `settings.clip_max_retries` times.
4. **stitch** — normalizes all clips to a common fps/resolution/codec, then joins them: `xfade` crossfade (`settings.crossfade_duration`) between independent scenes, hard concat within a chain (motion continuity would make a crossfade look wrong). The multi-clip (>2) path currently always uses plain concat rather than per-pair xfade — see the `else` branch in `stitch_clips` (`app/services/ffmpeg.py`).

### Provider abstraction

`app/providers/base.py` defines the `VideoProvider` Protocol (`generate_clip(image_path, prompt, duration_s, aspect_ratio, model, out_path) -> clip_path`; the caller owns the filename and guarantees its directory exists, so an implementation must overwrite `out_path` rather than invent a name — see rule 1 above). Two implementations: `FalProvider` (real fal.ai queue API, base64 data-URI image upload, model endpoint/cost lookup in `app/config.py`'s `model_endpoints`/`model_costs`) and `MockProvider` (ffmpeg-only, no network). `app/graph/nodes/generate_clips.py::_get_provider()` picks between them based on `settings.mock_provider` — that's the only switch point; adding a real provider means adding another class satisfying the Protocol and extending that dispatch (plus `model_endpoints`/`model_costs` entries).

### Config

All runtime settings are one frozen dataclass, `settings = Settings()` in `app/config.py`, populated from env vars at import time (not per-request) — includes API keys, `MOCK_PROVIDER`, upload limits, semaphore/retry counts, output resolution/fps, and the per-model cost table (`model_costs`) and fal.ai endpoint slugs (`model_endpoints`) used by both cost estimation and `FalProvider`.

### Frontend

`app/static/` is plain HTML/CSS/vanilla JS (no build step, no framework) served directly by FastAPI's `StaticFiles` mount at `/`. Five mutually exclusive screens driven by `app.js`, toggled only through `showScreen()`: login → upload form → scene plan editor (cost/duration recompute client-side-triggered via `/scenes/sync` and `/scenes/{idx}/update`) → progress screen consuming the SSE stream from `/jobs/{id}/events`, plus a "Moje filmy" list. The editor holds no `File` objects after a reload, so it rebuilds its thumbnails from `GET /jobs/{id}/images/{index}` and its picker from `image_count` in `GET /jobs/{id}`. `resumeJob()` subscribes to SSE *before* calling `/resume`, so an early event can't be missed.

## Relationship to the parent directory's docs

The parent directory (`/Users/pavcio/Desktop/video-generator/`) contains an unrelated, much larger PRD/spec package (in Polish) for a different, hypothetical multi-provider SaaS product ("VideoAI Studio") — that spec is aspirational and not implemented here. This repo is a separate, scoped-down PoC built to validate output quality and last-frame chaining cheaply; don't conflate the two or assume this code needs to match that spec's architecture (modular monolith, credit ledger, multi-provider adapter registry, etc.).
