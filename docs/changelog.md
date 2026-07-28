# Changelog

## [0.1.0] — 2026-07-28 — POC

Pierwsza dzialajaca wersja. Caly flow od uploadu zdjec do pobrania gotowego MP4.

### Backend

#### FastAPI + API REST
- `POST /jobs` — upload zdjec (jpg/png/webp, max 10 MB, max 30 sztuk) + prompt + model + dlugosc
- `POST /jobs/{id}/plan` — uruchamia planowanie scen (async w tle)
- `POST /jobs/{id}/generate` — akceptacja planu, start generacji klipow
- `POST /jobs/{id}/scenes/sync` — bulk edycja listy scen (dodawanie, usuwanie, zmiana kolejnosci)
- `POST /jobs/{id}/scenes/{idx}/update` — edycja pojedynczej sceny (sub_prompt, duration)
- `GET /jobs/{id}` — status joba + lista scen
- `GET /jobs/{id}/events` — SSE stream (real-time postep: validating → planning → generating → stitching → done)
- `GET /media/{id}/final.mp4` — pobranie gotowego filmu

#### Pipeline generacji (app/graph/)
- **validate** — walidacja formatow, rozmiarow; konwersja do RGB; resize+pad do 1280x720
- **plan_scenes** — planowanie scen przez LLM:
  - OpenAI (gpt-4o-mini) — priorytet
  - Anthropic (Claude Sonnet 4) — fallback
  - Mock planner — fallback gdy brak kluczy API (algorytmiczny podzial)
  - Wyjscie: lista scen z image_index, sub_prompt, duration_s, chain_from_prev
- **generate_clips** — generacja klipow wideo:
  - fal.ai provider (subscribe_async) — produkcyjny
  - Mock provider (FFmpeg: statyczne zdjecie + tekst) — testowy, $0
  - Last-frame chaining: ostatnia klatka klipu N = input klipu N+1
  - Lancuchy sekwencyjnie, miedzy lancuchami rownolegle (Semaphore=4)
  - Retry x2 przy bledzie
- **stitch** — FFmpeg: normalizacja klipow (24fps, 1280x720, H.264), concat/crossfade, output MP4

#### Providery wideo (app/providers/)
- `FalProvider` — fal.ai queue API (subscribe_async), upload obrazu jako base64 data URI
  - Modele: Wan, Kling 2.5 Turbo, Veo 3.1 Fast
- `MockProvider` — generuje klip FFmpegiem ze zdjecia (tekst promptu na dole), 2s delay

#### FFmpeg utilities (app/services/ffmpeg.py)
- `extract_last_frame` — ekstrakcja ostatniej klatki wideo jako PNG
- `normalize_clip` — ujednolicenie rozdzielczosci, fps, kodeka
- `get_video_duration` — odczyt dlugosci klipu
- `stitch_clips` — laczenie klipow: crossfade 0.5s miedzy scenami, hard cut w lancuchu

#### Baza danych (SQLite via aiosqlite)
- Tabela `jobs`: id, status, prompt, model, aspect_ratio, target_duration_s, est_cost_usd, error
- Tabela `scenes`: id, job_id, idx, image_path, sub_prompt, duration_s, chain_from_prev, status, clip_path, fal_request_id
- Statusy joba: uploaded → planning → planned → generating → stitching → done | error

#### Konfiguracja (app/config.py)
- Klucze API z env: FAL_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY
- MOCK_PROVIDER (true/false)
- Parametry wideo: rozdzielczosc 1280x720, 24fps, crossfade 0.5s
- Koszty modeli: Wan $0.05/s, Kling $0.07/s, Veo $0.10/s
- Limity: max 10 MB/zdjecie, max 30 zdjec, semaphore 4, retry 2

### Frontend (vanilla HTML + JS + CSS)

#### 3 ekrany:
1. **Formularz** — drag & drop zdjec (podglad miniaturek, usuwanie), textarea promptu, dropdown modelu, slider dlugosci 10-180s
2. **Plan scen** — lista scen z edycja: textarea sub_prompt, select dlugosci (5/10s), select zdjecia, przycisk "Usun"; przycisk "+ Dodaj scene"; koszt w $ + laczny czas; przyciski "Generuj" / "Wstecz"
3. **Postep** — badge statusu, pasek postepu, komunikat bledu; po ukonczeniu: player video + przycisk "Pobierz MP4"

#### Funkcjonalnosc:
- SSE (EventSource) — real-time aktualizacja statusu i postepu
- Dynamiczne przeliczanie kosztu i czasu przy edycji scen
- Sync scen z backendem przed generacja (POST /scenes/sync)

### Infrastruktura

- **Dockerfile**: python:3.12-slim + ffmpeg, pip install, uvicorn
- **docker-compose.yml**: port 8000, env_file .env, volume ./data + ./app (live reload)
- **Zaleznosci**: fastapi, uvicorn, langgraph, fal-client, anthropic, openai, aiosqlite, pillow, sse-starlette, python-multipart

### Znane ograniczenia (POC)

- Stan jobow trzymany w pamieci (`_job_states` dict) — restart = utrata aktywnych jobow
- Brak autentykacji — kazdy moze tworzyc joby
- Brak platnosci
- Brak czyszczenia starych plikow
- Brak retry sceny z UI
- Brak audio w wyjsciowym filmie
- Tylko rozdzielczosc 720p
