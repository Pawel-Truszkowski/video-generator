# Video Generator POC

Generator filmów ze zdjęć i promptu tekstowego. Klipy generowane przez fal.ai, zszywane FFmpegiem.

## Jak działa

1. Upload zdjęć + opis filmu
2. LLM (OpenAI/Anthropic) dzieli opis na sceny — każda scena to jedno zdjęcie + sub-prompt + czas trwania
3. Użytkownik edytuje plan: zmienia prompty, dodaje/usuwa sceny, wybiera zdjęcia
4. fal.ai generuje klipy wideo z każdej sceny (image-to-video)
5. Last-frame chaining — ostatnia klatka klipu N jest wejściem klipu N+1 (ciągłość ruchu)
6. FFmpeg zszywa klipy w finalny MP4

## Stack

- **Backend:** Python 3.12, FastAPI, aiosqlite
- **Video AI:** fal.ai (Wan, Kling 2.5 Turbo, Veo 3.1 Fast)
- **Scene planning:** OpenAI (gpt-4o-mini) / Anthropic (Claude Sonnet 4) / mock
- **Video processing:** FFmpeg
- **Frontend:** HTML + vanilla JS

## Szybki start

```bash
cp .env.example .env
# Uzupełnij klucze w .env

docker compose up --build
```

Otwórz http://localhost:8000

## Konfiguracja (.env)

| Zmienna | Opis |
|---|---|
| `FAL_KEY` | Klucz API fal.ai |
| `OPENAI_API_KEY` | Klucz OpenAI (planowanie scen) |
| `ANTHROPIC_API_KEY` | Klucz Anthropic (fallback planowania) |
| `MOCK_PROVIDER` | `true` = bez kosztów, klipy z FFmpeg; `false` = fal.ai |

## Modele wideo (fal.ai)

| Model | Koszt/s | ~3 min |
|---|---|---|
| Wan | $0.05 | ~$9 |
| Kling 2.5 Turbo | $0.07 | ~$13 |
| Veo 3.1 Fast | $0.10 | ~$18 |

## API

```
POST /jobs                         # upload zdjęć + prompt → {job_id}
POST /jobs/{id}/plan               # uruchom planowanie scen
POST /jobs/{id}/scenes/sync        # edycja listy scen (dodaj/usuń/zmień)
POST /jobs/{id}/scenes/{idx}/update # edycja pojedynczej sceny
POST /jobs/{id}/generate           # akceptacja planu, start generacji
GET  /jobs/{id}                    # status + sceny
GET  /jobs/{id}/events             # SSE (real-time postęp)
GET  /media/{id}/final.mp4         # pobranie filmu
```

## Struktura

```
app/
├── main.py                 # FastAPI + static
├── config.py               # ustawienia z env
├── db.py                   # aiosqlite (jobs, scenes)
├── api/jobs.py             # endpointy + SSE
├── graph/
│   ├── pipeline.py         # orkiestracja pipeline'u
│   └── nodes/              # validate, plan_scenes, generate_clips, stitch
├── providers/
│   ├── fal_provider.py     # fal.ai image-to-video
│   └── mock_provider.py    # testowy provider (FFmpeg)
├── services/ffmpeg.py      # concat, crossfade, ekstrakcja klatek
└── static/                 # frontend (index.html, app.js, style.css)
```
