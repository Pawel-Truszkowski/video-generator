# Video Generator

🇵🇱 Polski | [🇬🇧 English](README.en.md)

Aplikacja webowa, która zamienia zestaw zdjęć i opis tekstowy w gotowy film MP4.
Każda scena powstaje jako klip *image-to-video* w modelach udostępnianych przez
[fal.ai](https://fal.ai) (Wan, Kling, Veo), a całość jest składana FFmpegiem
w jeden plik z płynnymi przejściami.

Projekt jest działającym proof-of-concept rozwijanym w stronę MVP: ma konta
użytkowników, system kredytowy z płatnościami Stripe, panel administratora,
wznawianie przerwanych generacji i automatyczne czyszczenie danych. Aktualna
wersja: **0.8.0** — historia zmian w [`docs/changelog.md`](docs/changelog.md).

---

## Spis treści

- [Funkcje](#funkcje)
- [Jak to działa](#jak-to-działa)
- [Stack technologiczny](#stack-technologiczny)
- [Szybki start](#szybki-start)
- [Konfiguracja](#konfiguracja)
- [Modele wideo i cennik](#modele-wideo-i-cennik)
- [API](#api)
- [Architektura](#architektura)
- [Struktura projektu](#struktura-projektu)
- [Wdrożenie produkcyjne](#wdrożenie-produkcyjne)
- [Weryfikacja zmian](#weryfikacja-zmian)
- [Ograniczenia i roadmapa](#ograniczenia-i-roadmapa)
- [Dokumentacja](#dokumentacja)

---

## Funkcje

- **Dwa tryby planowania scen**
  - *ręczny* (domyślny) — karta sceny dla każdego zdjęcia: opis ruchu, długość,
    łączenie z poprzednią sceną; opcjonalny styl doklejany do każdej sceny,
  - *AI* — LLM dzieli opis filmu na sceny, a użytkownik zatwierdza i edytuje plan
    przed generacją.
- **Last-frame chaining** — ostatnia klatka klipu N staje się obrazem wejściowym
  klipu N+1, co zachowuje ciągłość ruchu między scenami.
- **Postęp w czasie rzeczywistym** przez Server-Sent Events.
- **Odporność na awarie** — stan jobów w SQLite, wznawianie po restarcie
  kontenera bez ponownego płacenia za gotowe klipy, ponawianie pojedynczej sceny,
  timeouty i czytelne błędy providera.
- **Logowanie bez hasła** — magic link wysyłany e-mailem (Resend), sesja
  w podpisanym HMAC ciasteczku `httpOnly`.
- **System kredytowy** — rezerwacja przed generacją, automatyczny zwrot za sceny,
  które nie powstały, doładowania przez Stripe Checkout, kredyty powitalne.
- **Panel administratora** — lista jobów i użytkowników, ręczne doładowania,
  blokowanie kont.
- **Retencja danych** — materiał roboczy usuwany po `RETENTION_DAYS`, gotowe
  filmy zostają do czasu usunięcia przez użytkownika.
- **Tryb mock** — cały przepływ działa lokalnie bez kluczy API i bez kosztów.

## Jak to działa

```
 zdjęcia + opis
       │
       ▼
┌─────────────┐   ┌──────────────┐   ┌────────────────┐   ┌──────────────┐
│  validate   │──►│ plan_scenes  │──►│ generate_clips │──►│    stitch    │──► final.mp4
│ Pillow,     │   │ ręcznie albo │   │ fal.ai i2v,    │   │ FFmpeg:      │
│ 1280×720    │   │ LLM          │   │ łańcuchy scen  │   │ xfade/concat │
└─────────────┘   └──────┬───────┘   └────────────────┘   └──────────────┘
                         │
                 akceptacja / edycja
                 planu przez użytkownika
```

1. **Upload** — użytkownik wgrywa zdjęcia (do 30, maks. 10 MB każde) i wybiera
   model. Obrazy są konwertowane do RGB i dopasowywane (letterbox) do 1280×720.
2. **Plan** — w trybie ręcznym plan przychodzi razem z uploadem; w trybie AI
   planer (OpenAI `gpt-4o-mini` → Anthropic Claude Sonnet 4 → algorytmiczny
   fallback) tworzy listę scen. Plan LLM jest sanityzowany (dozwolone długości,
   zakres indeksów zdjęć), bo wpływa bezpośrednio na cenę.
3. **Generacja** — sceny są grupowane w łańcuchy. Łańcuchy biegną równolegle
   (`SEMAPHORE_LIMIT`), sceny wewnątrz łańcucha sekwencyjnie, bo każda potrzebuje
   ostatniej klatki poprzedniej. Każdy klip jest zapisywany od razu po
   wygenerowaniu — awaria w scenie 5 nie traci scen 0–4.
4. **Składanie** — klipy są normalizowane do wspólnych fps/rozdzielczości/kodeka
   i łączone: crossfade między niezależnymi scenami, twarde cięcie wewnątrz
   łańcucha.
5. **Rozliczenie** — po zakończeniu (także przy błędzie lub zamknięciu kontenera)
   job jest rozliczany, a kredyty za niewygenerowane sceny wracają na konto.

## Stack technologiczny

| Warstwa | Technologia |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn, `sse-starlette` |
| Baza danych | SQLite (`aiosqlite`) |
| Generacja wideo | fal.ai queue API (`fal-client`) |
| Planowanie scen | OpenAI `gpt-4o-mini`, Anthropic Claude Sonnet 4 |
| Przetwarzanie | FFmpeg, Pillow |
| E-mail | Resend (HTTP API) |
| Płatności | Stripe Checkout + webhooki (HTTP API, bez SDK) |
| Frontend | HTML, CSS, vanilla JavaScript — bez frameworka i kroku budowania |
| Uruchomienie | Docker, Docker Compose |

## Szybki start

**Wymagania:** Docker z Docker Compose v2. Klucze API nie są potrzebne do
uruchomienia w trybie mock.

```bash
git clone <adres-repozytorium>
cd video-generator
cp .env.example .env          # domyślnie MOCK_PROVIDER=true — bez kosztów
docker compose up --build
```

1. Otwórz <http://localhost:8000>.
2. Podaj dowolny adres e-mail. Przy `MAIL_PROVIDER=console` link logujący pojawi
   się w logach kontenera (i w odpowiedzi HTTP) — otwórz go w przeglądarce.
3. Wgraj kilka zdjęć, opisz sceny i kliknij **Generuj**. W trybie mock każdy
   klip to statyczne zdjęcie z wypalonym tekstem promptu, wygenerowane lokalnie
   przez FFmpeg.

> **Uwaga dla developerów:** katalog `./app` jest podmontowany do kontenera, ale
> Uvicorn działa bez `--reload`. Zmiany w Pythonie wymagają
> `docker compose restart app`; pliki z `app/static/` są serwowane na bieżąco
> (wystarczy twarde odświeżenie przeglądarki).

### Praca z prawdziwymi modelami

Ustaw w `.env` `MOCK_PROVIDER=false` oraz `FAL_KEY` i przynajmniej jeden z kluczy
`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`. Od tej chwili każda generacja jest
**płatnym wywołaniem** fal.ai — sprawdź cennik poniżej.

## Konfiguracja

Cała konfiguracja pochodzi ze zmiennych środowiskowych, wczytywanych raz przy
starcie do niemutowalnego obiektu `settings` (`app/config.py`). Pełne,
skomentowane szablony: [`.env.example`](.env.example) (lokalnie) i
[`.env.prod.example`](.env.prod.example) (produkcja).

### Providerzy AI

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `MOCK_PROVIDER` | `true`¹ | `true` — lokalny planer i klipy z FFmpeg, zero kosztów; `false` — prawdziwe API |
| `FAL_KEY` | — | Klucz fal.ai (generacja wideo) |
| `OPENAI_API_KEY` | — | Klucz OpenAI (planer scen, pierwszy wybór) |
| `ANTHROPIC_API_KEY` | — | Klucz Anthropic (planer scen, fallback) |
| `CLIP_TIMEOUT_S` | `600` | Limit czasu na wygenerowanie jednego klipu |
| `SEMAPHORE_LIMIT` | `4` | Liczba łańcuchów scen generowanych równolegle (na 1 vCPU: `1`) |

¹ Wartość z `.env.example`; bez ustawionej zmiennej aplikacja przyjmuje `false`.

### Uwierzytelnianie i e-mail

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `MAIL_PROVIDER` | `console` | `console` — link w logach i odpowiedzi HTTP (tylko dev); `resend` — prawdziwy e-mail |
| `RESEND_API_KEY` | — | Wymagany przy `MAIL_PROVIDER=resend` |
| `MAIL_FROM` | `onboarding@resend.dev` | Nadawca; na produkcji musi być w zweryfikowanej domenie |
| `BASE_URL` | `http://localhost:8000` | Adres, z którego budowany jest link logujący |
| `SESSION_SECRET` | — | Sekret HMAC sesji: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `COOKIE_SECURE` | `false` | `true` wyłącznie za HTTPS |
| `ADMIN_EMAILS` | — | Adresy administratorów, rozdzielone przecinkami |

### Kredyty i płatności

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `CREDIT_MARGIN` | `2.0` | Mnożnik między kosztem u providera a ceną dla użytkownika |
| `WELCOME_CREDITS` | `500` | Kredyty na start dla nowego konta (`0` wyłącza) |
| `STRIPE_ENABLED` | `false` | Przy `false` doładowania wykonuje tylko administrator |
| `STRIPE_SECRET_KEY` | — | Klucz API Stripe |
| `STRIPE_WEBHOOK_SECRET` | — | Sekret podpisu webhooka (inny niż klucz API) |

### Limity i retencja

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `MAX_ACTIVE_JOBS_PER_USER` | `3` | Ile jobów jednego użytkownika może pracować jednocześnie |
| `RETENTION_DAYS` | `7` | Po ilu dniach usuwany jest materiał roboczy (uploady, klatki, klipy) |
| `CLEANUP_INTERVAL_H` | `24` | Częstotliwość pętli sprzątającej (pierwszy przebieg zawsze przy starcie) |

### Zabezpieczenia konfiguracji

Aplikacja **odmawia startu** przy niebezpiecznych kombinacjach ustawień:

- `COOKIE_SECURE=true` bez `SESSION_SECRET`,
- `COOKIE_SECURE=true` z `MAIL_PROVIDER=console` — tryb konsolowy zwraca link
  logujący w odpowiedzi HTTP, więc pozwala zalogować się na dowolne konto,
- `MAIL_PROVIDER=resend` bez `RESEND_API_KEY` lub ze współdzielonym nadawcą
  `@resend.dev`.

## Modele wideo i cennik

1 kredyt = 1 cent USD. Stawka kredytowa jest **wyliczana** z kosztu fal.ai
i `CREDIT_MARGIN` — nie istnieje druga tabela cen, która mogłaby się rozjechać
z cennikiem providera.

| Model | Endpoint fal.ai | Koszt fal.ai | Kredyty / s (marża 2.0) | Film 3 min (koszt fal.ai) |
|---|---|---|---|---|
| Wan 2.5 | `fal-ai/wan-25-preview/image-to-video` | $0.05/s | 10 | ~$9 |
| Kling 2.5 Turbo Pro | `fal-ai/kling-video/v2.5-turbo/pro/image-to-video` | $0.07/s | 14 | ~$13 |
| Veo 3.1 Fast | `fal-ai/veo3.1/fast/image-to-video` | $0.10/s | 20 | ~$18 |

Sceny mają długość 5 lub 10 s. Veo przyjmuje tylko 4/6/8 s, więc długość jest
mapowana na najbliższą dozwoloną wartość (nigdy dłuższą niż zaplanowana).

Pakiety doładowań (Stripe): **Starter** $5 → 500 kr., **Standard** $15 → 1650 kr.,
**Pro** $50 → 6000 kr.

## API

Interaktywna dokumentacja OpenAPI jest dostępna pod <http://localhost:8000/docs>.

Wszystkie endpointy poza `/auth/*`, `/health` i webhookiem Stripe wymagają
sesji. Endpointy dotyczące joba sprawdzają właściciela i dla cudzego `job_id`
zwracają **404** (nie 403), żeby identyfikatorów nie dało się enumerować.

<details>
<summary><strong>Uwierzytelnianie</strong></summary>

| Metoda | Ścieżka | Opis |
|---|---|---|
| `POST` | `/auth/request-login` | Wysyła link logujący na podany e-mail (z limitem prób) |
| `GET` | `/auth/callback?token=…` | Zużywa token, ustawia ciasteczko sesji, przekierowuje na `/` |
| `GET` | `/auth/me` | Dane zalogowanego użytkownika |
| `POST` | `/auth/logout` | Kończy sesję |

</details>

<details>
<summary><strong>Joby i generacja</strong></summary>

| Metoda | Ścieżka | Opis |
|---|---|---|
| `POST` | `/jobs` | Upload zdjęć + model + tryb planu (`manual` z listą scen lub `auto`) |
| `GET` | `/jobs` | Lista filmów użytkownika |
| `GET` | `/jobs/{id}` | Status joba i lista scen |
| `DELETE` | `/jobs/{id}` | Usuwa job wraz z plikami i gotowym filmem |
| `POST` | `/jobs/{id}/plan` | Uruchamia planowanie AI (tylko tryb `auto`) |
| `POST` | `/jobs/{id}/scenes/sync` | Zastępuje listę scen (dodawanie, usuwanie, kolejność, łańcuchy) |
| `POST` | `/jobs/{id}/scenes/{idx}/update` | Edycja pojedynczej sceny |
| `POST` | `/jobs/{id}/generate` | Akceptuje plan, rezerwuje kredyty i startuje generację |
| `POST` | `/jobs/{id}/scenes/{idx}/retry` | Ponawia generację jednej sceny |
| `POST` | `/jobs/{id}/resume` | Wznawia przerwany job od właściwego etapu |
| `GET` | `/jobs/{id}/events` | Strumień SSE z postępem |
| `GET` | `/jobs/{id}/thumbnail` | Miniatura joba |
| `GET` | `/jobs/{id}/images/{index}` | Wgrane zdjęcie o danym indeksie |
| `GET` | `/media/{id}/final.mp4` | Pobranie gotowego filmu |

</details>

<details>
<summary><strong>Kredyty</strong></summary>

| Metoda | Ścieżka | Opis |
|---|---|---|
| `GET` | `/credits` | Saldo i historia operacji |
| `GET` | `/credits/packages` | Dostępne pakiety doładowań |
| `POST` | `/credits/checkout` | Tworzy sesję Stripe Checkout |
| `POST` | `/credits/webhook` | Webhook Stripe (uwierzytelniany podpisem HMAC, bez sesji) |

</details>

<details>
<summary><strong>Administracja</strong> (wymaga konta z <code>ADMIN_EMAILS</code>)</summary>

| Metoda | Ścieżka | Opis |
|---|---|---|
| `GET` | `/admin/jobs` | Wszystkie joby |
| `DELETE` | `/admin/jobs/{id}` | Usunięcie dowolnego joba |
| `GET` | `/admin/users` | Lista użytkowników z saldami |
| `POST` | `/admin/users/{id}/credits` | Ręczne doładowanie lub korekta salda |
| `POST` | `/admin/users/{id}/active` | Blokada / odblokowanie konta |

</details>

<details>
<summary><strong>Monitoring</strong></summary>

| Metoda | Ścieżka | Opis |
|---|---|---|
| `GET` | `/health` | Stan bazy i wolne miejsce na dysku; `503` przy problemie |

</details>

## Architektura

Aplikacja to **jeden kontener**: FastAPI z pojedynczym workerem Uvicorna,
SQLite jako jedyne źródło prawdy i zadania w tle uruchamiane przez
`asyncio.create_task`. Taki układ jest świadomie dobrany do małego VPS-a
(1 vCPU, 1 GB RAM); przejście na kolejkę zadań, S3 i PostgreSQL jest opisane
w roadmapie jako faza skalowania.

Najważniejsze decyzje projektowe:

- **Stan joba żyje wyłącznie w SQLite.** Nie ma rejestru w pamięci — każde
  zadanie w tle odtwarza stan z bazy (`app/services/job_state.py`). Po restarcie
  joby, które były w trakcie pracy, dostają status `interrupted` i można je
  wznowić.
- **Scena jest gotowa, gdy jej plik istnieje na dysku**, a nie tylko gdy baza
  tak twierdzi. Ścieżka klipu jest czystą funkcją `(job_id, idx)`, dzięki czemu
  ponowienie jest idempotentne i nie płaci się dwa razy za ten sam klip.
- **Saldo kredytów jest zmieniane atomowym `UPDATE … WHERE balance >= ?`**,
  a tabela `credits` jest dziennikiem audytowym (append-only). Rozliczenie joba
  wynika z wpisów z jego `job_id`, jest idempotentne i wywoływane w `finally`,
  więc działa także przy zamknięciu kontenera.
- **Abstrakcja providera** — protokół `VideoProvider` (`app/providers/base.py`)
  z implementacjami `FalProvider` i `MockProvider`. Nowy model to wpis
  w `model_endpoints` / `model_costs` / `model_params`; nowy dostawca to nowa
  klasa spełniająca protokół.
- **Sesja w ciasteczku, nie w nagłówku `Authorization`** — frontend korzysta
  z `EventSource`, który nie pozwala ustawić nagłówków.

> Katalog `app/graph/` nosi nazwę z czasów prototypu opartego na LangGraph.
> Obecnie pipeline to zwykłe funkcje asynchroniczne wywoływane po kolei —
> LangGraph nie jest zależnością projektu.

## Struktura projektu

```
.
├── app/
│   ├── main.py                 # aplikacja FastAPI, lifespan, /health, montaż statyków
│   ├── config.py               # ustawienia z env, modele, cennik, pakiety
│   ├── db.py                   # schemat SQLite i migracje
│   ├── api/
│   │   ├── auth.py             # magic link, sesje, rate limiting
│   │   ├── jobs.py             # joby, sceny, generacja, wznawianie, SSE, media
│   │   ├── credits.py          # saldo, pakiety, Stripe Checkout i webhook
│   │   └── admin.py            # panel administratora
│   ├── auth/                   # tokeny HMAC, użytkownicy, zależności (require_user, get_owned_job)
│   ├── mail/                   # wysyłka linków: konsola (dev) i Resend (prod)
│   ├── graph/
│   │   ├── pipeline.py         # orkiestracja etapów i zdarzenia SSE
│   │   └── nodes/              # validate, plan_scenes, generate_clips, stitch
│   ├── providers/              # VideoProvider: fal.ai i mock
│   ├── services/
│   │   ├── job_state.py        # granica DB ⇄ stan pipeline'u
│   │   ├── credits.py          # jedyny moduł zapisujący saldo i ledger
│   │   ├── stripe_client.py    # klient Stripe na httpx
│   │   ├── cleanup.py          # retencja i usuwanie plików
│   │   └── ffmpeg.py           # normalizacja, concat, crossfade, ekstrakcja klatek
│   └── static/                 # frontend: index.html, app.js, style.css
├── docs/
│   ├── changelog.md            # historia wersji z uzasadnieniem decyzji
│   ├── roadmap.md              # plan rozwoju do MVP
│   └── deployment.md           # instrukcja wdrożenia na VPS
├── scripts/backup-db.sh        # kopia zapasowa SQLite (do crona)
├── docker-compose.yml          # środowisko developerskie
├── docker-compose.prod.yml     # środowisko produkcyjne
├── Dockerfile
└── requirements.txt
```

Dane w czasie działania trafiają do woluminu `./data` (montowanego jako `/data`):
baza `video_gen.db`, katalogi `uploads/`, `frames/`, `clips/` (materiał roboczy,
objęty retencją) oraz `final/` (gotowe filmy).

## Wdrożenie produkcyjne

Produkcja korzysta z osobnego pliku Compose:

```bash
cp .env.prod.example .env      # uzupełnij sekrety
docker compose -f docker-compose.prod.yml up -d --build
```

Różnice względem środowiska developerskiego:

- kod jest wbudowany w obraz (brak bind-mounta `./app`) — aktualizacja wymaga
  `git pull` **i** `--build`,
- `restart: unless-stopped`, healthcheck na `/health`, rotacja logów
  i limit pamięci kontenera,
- Uvicorn z `--proxy-headers` — TLS terminuje reverse proxy przed aplikacją.

Minimalne wymagania produkcyjne: HTTPS, `COOKIE_SECURE=true`,
`MAIL_PROVIDER=resend` z nadawcą w zweryfikowanej domenie, losowy
`SESSION_SECRET`. Na serwerze z 1 vCPU ustaw `SEMAPHORE_LIMIT=1`.

Kopia zapasowa bazy (spójna, przez `sqlite3.backup()`, rotacja 7 dni):

```bash
0 3 * * * cd /opt/video-generator && ./scripts/backup-db.sh >> /var/log/vg-backup.log 2>&1
```

Szczegółowa instrukcja krok po kroku: [`docs/deployment.md`](docs/deployment.md).

## Weryfikacja zmian

Projekt nie ma automatycznego zestawu testów. Zmiany weryfikuje się ręcznie,
w trybie mock:

1. `docker compose up --build`, zaloguj się linkiem z logów.
2. Przejdź pełny przepływ: upload → plan → generacja → pobranie filmu.
3. Błędy etapów pipeline'u są logowane ze stack trace'em w logach kontenera.
4. **Wznawianie:** zrestartuj kontener w trakcie generacji
   (`docker compose restart app`) i kliknij *Wznów* w „Moje filmy”.
5. **Retencja:** ustaw `RETENTION_DAYS=0` i zrestartuj kontener — sprzątanie
   wykona się przy starcie.

## Ograniczenia i roadmapa

Świadome ograniczenia obecnej wersji:

- pojedynczy proces i SQLite — brak skalowania horyzontalnego,
- pliki na dysku lokalnym (bez S3/CDN),
- wyjście tylko 1280×720, 16:9, bez ścieżki audio,
- brak powiadomień e-mail o zakończeniu generacji,
- brak testów automatycznych.

Plan rozwoju (stabilność → konta → płatności → deploy → UX → skalowanie) oraz
lista znanych błędów z code review: [`docs/roadmap.md`](docs/roadmap.md).

## Dokumentacja

| Dokument | Zawartość |
|---|---|
| [`docs/changelog.md`](docs/changelog.md) | Historia wersji wraz z uzasadnieniem kluczowych decyzji |
| [`docs/roadmap.md`](docs/roadmap.md) | Fazy rozwoju, status zadań, znane problemy |
| [`docs/deployment.md`](docs/deployment.md) | Wdrożenie na VPS, HTTPS, limity pamięci, przełączenie na fal.ai |
| [`CLAUDE.md`](CLAUDE.md) | Szczegółowe reguły architektury i niezmienniki kodu |
| [`plan-implementacji.md`](plan-implementacji.md) | Pierwotny plan PoC (historyczny) |
