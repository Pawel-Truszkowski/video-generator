# Changelog

## [0.2.0] — 2026-08-01 — Logowanie i konta uzytkownikow (roadmap Faza 2.1 + 2.2)

Do tej pory kazdy kto znal 12-znakowy `job_id` mogl czytac cudze prompty, edytowac
sceny, pobierac filmy i — przez `POST /jobs/{id}/generate` — wydawac cudze pieniadze
na fal.ai. Teraz kazdy job ma wlasciciela.

### Logowanie (magic link, bez hasel)
- `POST /auth/request-login` — podajesz email, dostajesz jednorazowy link (wazny 15 min)
  - Rejestracja = logowanie: nie da sie sprawdzic czy dany email ma konto
  - Rate limit: 3 proby / 15 min na email, 10 / 15 min na IP (in-process, bez Redisa)
- `GET /auth/callback?token=...` — ustawia ciasteczko sesji, przekierowuje na `/`
- `GET /auth/me` — kto jest zalogowany (bootstrap frontendu)
- `POST /auth/logout` — czysci ciasteczko (idempotentne)

### Tokeny
- Magic link: `secrets.token_urlsafe(32)`, w bazie tylko SHA-256 — wyciek bazy nie daje
  dzialajacych linkow
- Jednorazowosc przez atomowy `UPDATE ... WHERE used_at IS NULL` + `rowcount`
  (SELECT-potem-UPDATE bylby wyscigiem nawet na jednym polaczeniu)
- Nowy link uniewaznia poprzedni — w danym momencie zyje max 1 token na uzytkownika
- Sesja: `v1.<payload>.<HMAC-SHA256>`, termin waznosci **w podpisanym payloadzie**
  (samo `Max-Age` ciasteczka klient moze zignorowac)
- Ciasteczko: `HttpOnly`, `SameSite=Lax`, `Secure` sterowane przez `COOKIE_SECURE`
  - `Lax` a nie `Strict`, bo klikniecie linku z poczty to nawigacja cross-site
  - Ciasteczko (a nie naglowek `Authorization`), bo `EventSource` nie umie ustawiac
    naglowkow — SSE dziala bez zmian w kodzie frontendu

### Autoryzacja
- `APIRouter(dependencies=[Depends(require_user)])` — kazda obecna i przyszla trasa
  w `jobs.py` jest chroniona z automatu
- `get_owned_job` — jedno miejsce sprawdzajace wlasnosc, uzyte przez wszystkie 8 tras
  - Zwraca **404, nie 403**, z tym samym komunikatem dla "nie istnieje" i "nie twoje",
    zeby `GET /jobs/<zgadywanka>` nie byl wyrocznia o istnieniu jobow
  - Walidacja `^[0-9a-f]{12}$` + budowanie sciezek z `job["id"]` zamyka path traversal
    w `/media/{job_id}/final.mp4`
- `/scenes/{idx}/update` i `/scenes/sync` w ogole nie dotykaly bazy — teraz sprawdzaja
  wlasciciela

### Nowe endpointy
- `GET /jobs` — lista jobow uzytkownika (+ liczba scen, laczny czas, `video_url`
  tylko gdy plik faktycznie istnieje na dysku)
- `GET /jobs/{id}/thumbnail` — miniaturka z `data/uploads/`, `Cache-Control: private`

### Wysylka maili (`app/mail/`)
- Protokol `MailSender` + dwie implementacje, wzorowane na `app/providers/`
- `MAIL_PROVIDER=console` (domyslne) — link do logow i do odpowiedzi HTTP.
  **Backdoor deweloperski**: podwojnie zabezpieczony (`console` + `COOKIE_SECURE=false`),
  a start z `COOKIE_SECURE=true` + `console` konczy sie bledem
- `MAIL_PROVIDER=resend` — Resend HTTP API

### Baza danych
- Nowe tabele `users`, `magic_tokens` (+ indeksy)
- `jobs` dostaje `user_id` i `created_at` przez idempotentna migracje w `_migrate()`
  (`PRAGMA table_info` → warunkowy `ALTER TABLE`), bo `CREATE TABLE IF NOT EXISTS`
  nie dodaje kolumn do istniejacej tabeli
- Indeks `idx_jobs_user_created` tworzony **po** migracji — w `SCHEMA` wywalilby sie
  na `no such column: user_id` i zabral ze soba tworzenie `users`/`magic_tokens`
- Daty jako `strftime('%Y-%m-%dT%H:%M:%SZ')` — `datetime('now')` nie ma znacznika
  strefy i `new Date()` w przegladarce zinterpretowalby je jako czas lokalny
- 11 istniejacych jobow ma `user_id = NULL` → sa niewidoczne dla wszystkich
  (`WHERE user_id = ?` nigdy nie trafia w NULL). Nikt nie zostanie obciazony
  za juz wydane pieniadze.

### Frontend
- Nowe ekrany: logowanie i "Moje filmy"; `#screen-form` startuje ukryty, zeby
  wylogowany uzytkownik nie zobaczyl mignięcia formularza
- `showScreen()` zastepuje trzy miejsca recznie przelaczajace klase `.hidden`
- `apiFetch()` — 401 na dowolnym wywolaniu wraca na ekran logowania zamiast cichej awarii
  (wczesniej `catch (_) {}` przy edycji scen zjadal wszystko)
- `errText()` wyciaga `detail` z odpowiedzi FastAPI, w tym komunikat walidacji z 422
- Wylogowanie czysci `files`/`jobId`/`currentScenes` — inaczej kolejny uzytkownik
  na tej samej przegladarce odziedziczylby wgrane zdjecia poprzednika
- Obsluga 401 na strumieniu SSE (wczesniej ekran postepu wisialby w nieskonczonosc)

### Konfiguracja
- Nowe: `MAIL_PROVIDER`, `RESEND_API_KEY`, `MAIL_FROM`, `BASE_URL`, `SESSION_SECRET`,
  `COOKIE_SECURE`
- Brak `SESSION_SECRET` → staly sekret deweloperski + ostrzezenie (staly, nie losowy:
  losowy wylogowywalby wszystkich przy kazdym restarcie)
- Start aplikacji przerywany gdy: `COOKIE_SECURE=true` bez `SESSION_SECRET`,
  `COOKIE_SECURE=true` z `MAIL_PROVIDER=console`, albo `resend` bez klucza API
- Nowa zaleznosc: `httpx` (tylko do Resend). Sesje i tokeny na samej bibliotece
  standardowej — bez `pyjwt`, `passlib`, `itsdangerous`

### Znane ograniczenia
- Rate limit trzymany w pamieci procesu (reset po restarcie); za reverse proxy
  `request.client.host` to IP proxy, wiec Faza 4 musi wlaczyc `--proxy-headers`
- Skanery linkow w firmowej poczcie (np. Outlook Safe Links) moga "kliknac" link
  zanim zrobi to czlowiek i go zuzyc. Nie wystepuje przy `MAIL_PROVIDER=console`.
- "Moje filmy" jest tylko do odczytu — wznowienie niedokonczonego joba wymaga
  przeniesienia `_job_states` do bazy (Faza 1.1)
- `PRAGMA foreign_keys` nadal wylaczone, wiec `REFERENCES` jest dekoracyjne
- Brak panelu admina (Faza 2.3) i platnosci (Faza 3)

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
