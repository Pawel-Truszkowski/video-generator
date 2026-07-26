# Plan implementacji PoC — Generator filmów ze zdjęć i promptu

**Stack:** Python 3.12, FastAPI, LangGraph, Docker Compose, fal.ai, FFmpeg, frontend HTML+JS (vanilla).
**Cel:** sprawdzić, czy film 3–4 min ze zdjęć + opisu jest wystarczającej jakości.

## 1. Jak to działa

fal.ai generuje klipy ~5–10 s z jednego zdjęcia, więc długi film składamy z segmentów:

1. LLM dzieli opis na sceny (zdjęcie + pod-prompt + długość klipu).
2. Klipy generowane równolegle przez fal.ai.
3. Ciągłość: **last-frame chaining** — ostatnia klatka klipu N jest obrazem wejściowym klipu N+1.
4. FFmpeg zszywa całość (concat + crossfade 0,5 s).

## 2. Architektura

```
[index.html + app.js]
   │  POST /jobs (zdjęcia + prompt)     GET /jobs/{id}/events (SSE)
   ▼
[FastAPI :8000] → job w tle (asyncio)
   ▼
[LangGraph: validate → plan_scenes → generate_clips → stitch → finalize]
   ├─► fal.ai queue API
   ├─► LLM (plan scen)
   └─► FFmpeg (subprocess)
   ▼
[SQLite: joby/sceny]   [/data: uploady, klipy, final.mp4]
```

Jeden kontener, bez Redisa/Celery. Bez rejestracji — job po UUID.

## 3. Struktura repo

```
video-gen/
├── docker-compose.yml
├── Dockerfile                  # python:3.12-slim + ffmpeg
├── .env                        # FAL_KEY, ANTHROPIC_API_KEY, MOCK_PROVIDER
└── app/
    ├── main.py                 # FastAPI + static
    ├── config.py
    ├── db.py                   # aiosqlite
    ├── api/jobs.py             # endpointy + SSE
    ├── graph/
    │   ├── state.py
    │   ├── pipeline.py
    │   └── nodes/  (validate, plan_scenes, generate_clips, stitch, finalize)
    ├── providers/
    │   ├── base.py             # VideoProvider (Protocol)
    │   ├── fal_provider.py
    │   └── mock_provider.py    # testowy MP4 za 0 $
    ├── services/ffmpeg.py      # concat, crossfade, ekstrakcja ostatniej klatki
    └── static/  (index.html, app.js, style.css)
```

Zależności: `fastapi`, `uvicorn`, `langgraph`, `langgraph-checkpoint-sqlite`, `fal-client`, `anthropic`, `aiosqlite`, `python-multipart`, `sse-starlette`, `pillow`.

## 4. Dane (SQLite)

```sql
jobs   (id, status, prompt, model, aspect_ratio, target_duration_s, est_cost_usd, error)
scenes (id, job_id, idx, image_path, sub_prompt, duration_s, chain_from_prev,
        status, clip_path, fal_request_id)
```

Statusy joba: `uploaded → planning → planned → generating → stitching → done | error`.

## 5. Węzły pipeline'u

- **validate** — jpg/png/webp, max 10 MB, max 30 zdjęć; Pillow: RGB, resize+pad do 1280×720.
- **plan_scenes** — jedno wywołanie LLM (JSON mode): `[{image_index, sub_prompt, duration_s (5|10), chain_from_prev}]`; suma ≈ target, każde zdjęcie użyte ≥1×. Wyliczenie kosztu, status `planned`, **interrupt** — czeka na akceptację usera.
- **generate_clips** — po akceptacji: fan-out z `asyncio.Semaphore(4)`; łańcuchy (`chain_from_prev`) sekwencyjnie, między łańcuchami równolegle; `fal_client.submit_async` + polling co 5 s, retry ×2; po klipie ekstrakcja ostatniej klatki dla następnego ogniwa.
- **stitch** — normalizacja (`-r 24 -s 1280x720 -c:v libx264 -pix_fmt yuv420p`), `xfade` 0,5 s między scenami, twarde cięcie w łańcuchu.
- **finalize** — `data/final/{job_id}.mp4`, event SSE.

Checkpointing LangGraph (SqliteSaver): wznowienie po błędzie bez ponownego płacenia za gotowe klipy.

## 6. fal.ai — modele i koszty

| UI | Endpoint (zweryfikować w docs na starcie) | ~$/s | 3 min |
|---|---|---|---|
| `wan` | `fal-ai/wan-i2v` | 0,05 | ~9 $ |
| `kling-2.5-turbo` | `fal-ai/kling-video/v2.5-turbo/pro/image-to-video` | 0,07 | ~13 $ |
| `veo-3.1-fast` | `fal-ai/veo3.1/fast/image-to-video` | 0,10 | ~18 $ |

Koszt pokazywany w UI **przed** generacją. `MOCK_PROVIDER=true` → cały pipeline bez kosztów.

## 7. API

```
POST /jobs                        # images[], prompt, model, aspect, target_duration → {job_id}
POST /jobs/{id}/plan              # → {scenes[], est_cost_usd}
POST /jobs/{id}/generate          # akceptacja planu, start
POST /jobs/{id}/scenes/{idx}/retry
GET  /jobs/{id}                   # status + sceny + postęp
GET  /jobs/{id}/events            # SSE
GET  /media/{job_id}/final.mp4
```

## 8. Frontend (3 ekrany, ~300 linii JS, bez frameworka)

1. **Formularz:** drag&drop zdjęć (podgląd, kolejność), textarea promptu, dropdown modelu, długość (60/120/180/240 s), „Zaplanuj".
2. **Plan:** lista scen (miniatura, edytowalny pod-prompt, długość), koszt w $, „Generuj".
3. **Postęp:** pasek per scena (EventSource), na końcu `<video controls>` + pobranie; przy błędzie „Ponów scenę".

## 9. Docker

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app/ ./app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```yaml
services:
  app:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    volumes: ["./data:/data"]
```

## 10. Kolejność pracy (~4–5 dni)

1. **Szkielet (1 d):** Docker, FastAPI, upload, SQLite, formularz HTML.
2. **Pojedynczy klip (1 d):** fal_provider + mock, 1 zdjęcie → 1 klip, player w UI. *Tu pierwsza ocena jakości modelu.*
3. **Długi film (2 d):** plan_scenes (LLM), ekran akceptacji + koszt, fan-out, chaining, stitch.
4. **Wykończenie (0,5–1 d):** SSE, retry sceny, drobne poprawki UI.