# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A working proof-of-concept (not the "VideoAI Studio" SaaS spec from the parent directory's docs) that turns a set of uploaded images + a text prompt into a stitched MP4. Single FastAPI container, SQLite, magic-link auth, a credit ledger with Stripe top-ups — see `docs/roadmap.md` for what's explicitly deferred (credits/Stripe, admin panel, S3 storage, Celery/Redis queue, disk cleanup) and `docs/changelog.md` for what each version shipped.

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
4. **Only `app/services/cleanup.py` deletes a job's files.** It takes every path
   from `job_state` (`uploads_dir`, `frames_dir`, `clips_dir`, `final_path`) — a
   second place building those paths would silently stop matching what the pipeline
   writes. Two rules inside it: retention removes only the *work* material
   (uploads/frames/clips), never `data/final/*.mp4`, which goes away solely through
   `DELETE /jobs/{id}`; and anything that deletes both a row and files does the
   **row first** — leftover files are invisible and the orphan sweep collects them,
   whereas a leftover row shows up in "Moje filmy" offering buttons that can only
   fail. The orphan sweep's age threshold is load-bearing, not cautious:
   `create_job` makes `uploads/{job_id}` *before* the INSERT, so a job mid-upload
   momentarily looks exactly like an orphan.
5. **`jobs.workdirs_purged_at` is the only way to tell "cleaned" from "never had
   files".** `os.path.exists() == False` conflates purged / never-existed /
   unmounted volume, and those need different answers. `_assert_files_present()`
   in `app/api/jobs.py` turns it into a 409 on plan/generate/resume/retry; without
   it `Wznow` on an old job passes `_decide_stage()` and dies in a background task
   as a bare `error`.
6. **`_running` (`app/api/jobs.py`) is `dict[job_id, user_id]` and the per-user
   concurrency limit is counted from it, not from the DB.** The dict is read and
   written synchronously, so two concurrent requests cannot interleave between the
   check and the insert in `_start()`; a `SELECT COUNT(*)` over the active statuses
   would `await` right there and let a double-click past the limit.
7. **A `planned` job never resumes into generation by itself.** `_decide_stage()` returns `planning`/`planned`/`generating`/`done`; only the first and third start a task, because pressing *Generuj* is what spends money. A job that failed on one scene resumes as `generating`, not `planning` — replanning calls `save_scenes`, which is DELETE+INSERT and would drop paid-for clips. For the same reason plan edits are rejected with 409 once any scene has `status='done'`, not just on job status.

### Credits (roadmap Faza 3, shipped in 0.7.0)

1 credit = 1 cent USD, always an integer. Rates are **derived** from
`settings.model_costs` × `CREDIT_MARGIN` in `credits.credit_rate()` — there is no
second table of prices, because it would drift from fal.ai's the first time their
pricing changes. Note `ceil(round(x, 6))`, not bare `ceil()`: `0.07 * 100 * 2.0`
is `14.000000000000002` in floating point, which rounded Kling up by a full credit.

Six rules that are easy to break:

1. **`users.credit_balance` is authoritative; `SUM(credits.amount)` is an audit
   trail.** This is the opposite of the parent directory's spec and it follows
   from `app/db.py`: one shared aiosqlite connection, no explicit transactions,
   every function committing on its own. Only a single SQL statement is atomic
   here, so a charge is `UPDATE users SET credit_balance = credit_balance - ?
   WHERE id = ? AND credit_balance >= ? RETURNING credit_balance` — no row
   returned means no funds. `SELECT SUM` then `INSERT` is the same TOCTOU window
   `_assert_slot_free` and `consume_magic_token` both avoid.
2. **`app/services/credits.py` is the only module that writes either of them.**
   `grant()` and `try_charge()` update the balance and append the ledger row
   together. Note the orderings differ on purpose: `try_charge` updates first
   (the CAS *is* the check), `grant` inserts first, because `INSERT OR IGNORE` on
   `UNIQUE(ext_id)` is what makes a repeated Stripe webhook a no-op — with the
   balance moving first, the duplicate would already have been paid.
3. **A job's whole accounting derives from ledger rows carrying its `job_id`.**
   `refund = -SUM(amount WHERE job_id) - credits_for(model, scenes done)`. It is
   idempotent (a second call yields 0) and needs no separate reservation state,
   which is what makes retry and resume work: each adds its own `usage`.
4. **`settle_job()` is called from `finally`, never `except Exception`.**
   `asyncio.CancelledError` inherits from `BaseException`, so the existing
   handler in `run_generation` does not catch a container shutdown — the single
   most likely moment for a reservation to vanish. `reconcile_interrupted()` (now
   returning ids, not a count) is the second net, settling what died with the
   previous process; idempotency is why both can fire on the same job.
5. **Charging happens in the handler, before `_start()`** — not inside it, even
   though `_start` is the one gate every task passes. `_start` is synchronous by
   design (see the comment on `_assert_slot_free`) and a DB write needs `await`.
   In `retry_scene` the charge must come **before** the clip is deleted, for the
   same reason the slot check does.
6. **Resume and retry price only what is missing**, via
   `job_state.is_rendered()` — the same function `generate_clips` uses to skip
   scenes. Charging the full plan would bill a second time for clips already on
   disk.

Stripe runs on `httpx` with no SDK (`app/services/stripe_client.py`), like
`app/mail/resend_sender.py`. Two things to keep in mind: the webhook lives on its
**own router without `require_user`** (Stripe sends no session cookie; the HMAC
signature is the authentication), and the signature is computed over the **raw
request body** — call `await request.body()` before anything parses JSON. Credit
amounts come from our own package table by `metadata[package_id]`, never from the
payload's `amount_total`. `STRIPE_ENABLED=false` by default, so local work needs
no Stripe account; admins top accounts up through
`POST /admin/users/{id}/credits`.

Scene `duration_s` is an allowlist (`settings.allowed_durations`) in **both**
`/scenes/{idx}/update` and `SceneItem`, and LLM-authored plans go through
`_sanitize_plan()`. These are not tidiness: `duration_s` is the only dimension of
price, a negative one reserves a negative amount — that is, it credits the
account — and the user's prompt reaches the planner verbatim.

### Data retention (roadmap Faza 1.3, shipped in 0.5.0)

`cleanup.retention_loop()` is started in `lifespan` (`app/main.py`) — an asyncio
task, not cron, so it works locally and needs no per-server install. It runs a
sweep immediately at startup, then every `CLEANUP_INTERVAL_H` hours, which makes
`docker compose restart app` the supported way to force a cleanup (set
`RETENTION_DAYS=0` to purge everything on the next restart when testing). It is
started **after** `reconcile_interrupted()`: the sweep skips jobs in an active
status, and before that call jobs from the dead process still wear one.

### Pipeline stages (`app/graph/nodes/`)

1. **validate** — format/size checks, Pillow RGB convert + letterbox-resize to `settings.output_width/height` (1280×720), writes `*_processed.jpg` next to originals.
2. **plan_scenes** — LLM call (OpenAI gpt-4o-mini first, Anthropic Claude Sonnet 4 fallback, algorithmic `_mock_plan` last resort) turns the prompt + image count into a JSON scene list (`image_index`, `sub_prompt`, `duration_s` ∈ {5,10}, `chain_from_prev`). Also computes `est_cost_usd` from `settings.model_costs`.
3. **generate_clips** — groups scenes into chains on `chain_from_prev`; chains run in parallel (`asyncio.Semaphore(settings.semaphore_limit)`, default 4), scenes within a chain run sequentially because each needs the previous clip's last frame (`extract_last_frame`, ffmpeg) as its input image. Retries each scene up to `settings.clip_max_retries` times.
4. **stitch** — writes its scratch (normalized clips + the ffmpeg concat list) to
   `data/stitch/{job_id}/`, supplied by the node as `work_dir` and removed in a
   `finally`. Never to `data/final`: until 0.5.0 those files were named
   `normalized/norm_{i}.mp4` and `concat.txt` with no job id in them, so two
   concurrent stitches overwrote each other and produced a film spliced from both
   jobs with no error — and `data/final` is the one directory retention must never
   sweep by age, so the leftovers were unreachable. It normalizes all clips to a
   common fps/resolution/codec, then joins them: `xfade` crossfade (`settings.crossfade_duration`) between independent scenes, hard concat within a chain (motion continuity would make a crossfade look wrong). The multi-clip (>2) path currently always uses plain concat rather than per-pair xfade — see the `else` branch in `stitch_clips` (`app/services/ffmpeg.py`).

### Provider abstraction

`app/providers/base.py` defines the `VideoProvider` Protocol (`generate_clip(image_path, prompt, duration_s, aspect_ratio, model, out_path) -> clip_path`; the caller owns the filename and guarantees its directory exists, so an implementation must overwrite `out_path` rather than invent a name — see rule 1 above). Two implementations: `FalProvider` (real fal.ai queue API, base64 data-URI image upload, model endpoint/cost lookup in `app/config.py`'s `model_endpoints`/`model_costs`) and `MockProvider` (ffmpeg-only, no network). `app/graph/nodes/generate_clips.py::_get_provider()` picks between them based on `settings.mock_provider` — that's the only switch point; adding a real provider means adding another class satisfying the Protocol and extending that dispatch (plus `model_endpoints`/`model_costs` entries).

### Config

All runtime settings are one frozen dataclass, `settings = Settings()` in `app/config.py`, populated from env vars at import time (not per-request) — includes API keys, `MOCK_PROVIDER`, upload limits, semaphore/retry counts, output resolution/fps, and the per-model cost table (`model_costs`) and fal.ai endpoint slugs (`model_endpoints`) used by both cost estimation and `FalProvider`.

### Frontend

`app/static/` is plain HTML/CSS/vanilla JS (no build step, no framework) served directly by FastAPI's `StaticFiles` mount at `/`. Seven mutually exclusive screens driven by `app.js`, toggled only through `showScreen()`: login → upload form → scene plan editor (duration is recomputed client-side; the **cost is not** — `/scenes/sync` and `/scenes/{idx}/update` return `credits_cost` and the editor displays that number. Until 0.7.0 `app.js` kept its own copy of the price table and showed the wrong model's rate after a resume) → progress screen consuming the SSE stream from `/jobs/{id}/events`, plus a "Moje filmy" list. The editor holds no `File` objects after a reload, so it rebuilds its thumbnails from `GET /jobs/{id}/images/{index}` and its picker from `image_count` in `GET /jobs/{id}`. `resumeJob()` subscribes to SSE *before* calling `/resume`, so an early event can't be missed.

## Relationship to the parent directory's docs

The parent directory (`/Users/pavcio/Desktop/video-generator/`) contains an unrelated, much larger PRD/spec package (in Polish) for a different, hypothetical multi-provider SaaS product ("VideoAI Studio") — that spec is aspirational and not implemented here. This repo is a separate, scoped-down PoC built to validate output quality and last-frame chaining cheaply; don't conflate the two or assume this code needs to match that spec's architecture (modular monolith, credit ledger, multi-provider adapter registry, etc.).
