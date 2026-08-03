# Video Generator POC

Generator filmów ze zdjęć i promptu tekstowego. Klipy generowane przez fal.ai, zszywane FFmpegiem.

## Jak działa

0. Logowanie magic linkiem (podajesz email, klikasz link — bez hasła)
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
| `MAIL_PROVIDER` | `console` = link do logowania w logach (dev); `resend` = prawdziwy email |
| `RESEND_API_KEY` | Klucz Resend (tylko gdy `MAIL_PROVIDER=resend`) |
| `BASE_URL` | Adres używany do zbudowania linku w mailu |
| `SESSION_SECRET` | Sekret podpisujący ciasteczko sesji (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) |
| `COOKIE_SECURE` | `true` tylko za HTTPS — na `http://localhost` musi być `false` |

## Modele wideo (fal.ai)

| Model | Koszt/s | ~3 min |
|---|---|---|
| Wan | $0.05 | ~$9 |
| Kling 2.5 Turbo | $0.07 | ~$13 |
| Veo 3.1 Fast | $0.10 | ~$18 |

## API

Wszystkie endpointy `/jobs/*` i `/media/*` wymagają zalogowania (ciasteczko sesji)
i sprawdzają właściciela — cudzy `job_id` zwraca 404.

```
POST /auth/request-login           # email → wysyła link do logowania
GET  /auth/callback?token=...      # ustawia ciasteczko sesji, przekierowuje na /
GET  /auth/me                      # kto jest zalogowany
POST /auth/logout                  # czyści ciasteczko

POST /jobs                         # upload zdjęć + prompt → {job_id}
GET  /jobs                         # lista moich filmów
GET  /jobs/{id}/thumbnail          # miniaturka
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
├── main.py                 # FastAPI + routery + static (mount "/" MUSI być ostatni)
├── config.py               # ustawienia z env
├── db.py                   # aiosqlite (jobs, scenes, users, magic_tokens) + migracje
├── api/jobs.py             # endpointy + SSE
├── api/auth.py             # logowanie magic linkiem + rate limit
├── auth/
│   ├── tokens.py           # HMAC sesji, hashowanie tokenów
│   ├── store.py            # użytkownicy + tokeny (atomowe zużycie)
│   └── deps.py             # require_user, get_owned_job, ciasteczka
├── mail/
│   ├── console_sender.py   # link do logów (dev)
│   └── resend_sender.py    # Resend API (prod)
├── graph/
│   ├── pipeline.py         # orkiestracja pipeline'u
│   └── nodes/              # validate, plan_scenes, generate_clips, stitch
├── providers/
│   ├── fal_provider.py     # fal.ai image-to-video
│   └── mock_provider.py    # testowy provider (FFmpeg)
├── services/ffmpeg.py      # concat, crossfade, ekstrakcja klatek
└── static/                 # frontend (index.html, app.js, style.css)
```
