# Roadmap — Video Generator MVP

Stan: POC dziala (upload → plan scen → generacja klipow → stitch → MP4).
Cel: MVP ktore mozna pokazac klientom i pobierac oplate.

---

## Faza 1 — Stabilnosc i persystencja

Priorytet: KRYTYCZNY
Zaleznosci: brak

### 1.1 Persystencja stanu jobow — ZROBIONE (0.3.0)
- [x] Przeniesc `_job_states` (in-memory dict) do SQLite
- [x] Zapisywac `scenes`, `clip_paths`, `chain_flags`, `image_paths` w DB
      (`image_paths` odtwarzane z dysku, `clip_paths` = kolumna `scenes.clip_path`)
- [x] Po restarcie kontenera — mozliwosc wznowienia joba od ostatniego ukonzczonego kroku
- [x] Nie generowac ponownie klipow ktore juz istnieja na dysku

### 1.2 Obsluga bledow i retry
- [x] Endpoint `POST /jobs/{id}/scenes/{idx}/retry` — ponowna generacja pojedynczej sceny
- [x] Przycisk "Ponow" w UI przy scenie ze statusem error
      (+ przycisk "Podglad" na karcie w "Moje filmy" — bez niego przycisk "Ponow" byl
      osiagalny tylko wtedy, gdy uzytkownik siedzial na ekranie postepu w chwili awarii)
- [x] Timeout na generacje klipu (`CLIP_TIMEOUT_S`, domyslnie 600 s)
- [x] Globalny retry joba — przycisk "Wznow" jesli job upadl (dowiezione juz w 1.1)
- [ ] Scena nie pokazuje stanu posredniego: lista skacze z "Oczekuje" na "Gotowe", bo
      `generate_single` emituje tylko `on_scene_done`/`on_scene_error`. Brakuje trzeciego
      callbacku na START sceny + zapisu `scenes.status='generating'`, zeby podglad po
      odswiezeniu tez to pokazywal. Badge `generating` jest juz gotowy w UI.
- [ ] Dlugi `sub_prompt` jest obcinany w wierszu sceny (`.scene-row .scene-text` ma
      `white-space: nowrap` + `text-overflow: ellipsis` w `style.css`). Do wyboru:
      zawijanie, atrybut `title` jak przy komunikacie bledu, albo rozwijanie po kliknieciu.
- [x] Wyciek kolejek SSE — `release_event_queue()` wolane w `finally` generatora
      w `job_events`, klucz `job_id` znika po ostatnim subskrybencie, kolejka ma
      `maxsize=100`, a `_emit()` wyrejestrowuje kolejke, ktorej nikt nie opróznia.
- [x] Czytelne bledy providera — `ProviderError(message, retryable)` w
      `providers/base.py`, `_translate_fal_error()` w `fal_provider.py`. Bez tego
      422 z fal.ai wklejal caly request (z obrazem base64) do `scenes.error`, logow
      i UI. `content_policy_violation` i inne bledy walidacji nie sa ponawiane —
      identyczny request pada identycznie.
- [ ] Scena w lancuchu odrzucona przez filtr tresci na `image_url` (tj. na klatce
      z poprzedniego klipu, nie na uploadzie) — automatycznie sprobowac raz z
      oryginalnym obrazem sceny (`image_index`). Decyzja produktowa: traci sie
      ciaglosc ruchu. Przypadek z produkcji: job `de845d484e04` (artroskopia).

### 1.4 Znaleziska z code review (11.09.2026) — DO ZROBIENIA
Niezweryfikowane recznie — przed poprawka najpierw odtworzyc.
- [ ] **Wysoki:** `POST /jobs/{id}/plan` przyjmuje joby `error`/`interrupted`, ale
      nie sprawdza scen `done` (`app/api/jobs.py`, ~l. 215). `save_scenes` to
      DELETE+INSERT, wiec oplacone klipy wracaja do `pending` i fal.ai placi sie
      drugi raz. Edycja planu ma te blokade (`_assert_plan_editable`), replan nie —
      lamie regule 7 z CLAUDE.md
- [ ] **Wysoki:** `_decide_stage` (~l. 497) zwraca `generating` dla joba
      przerwanego w trakcie ponownego planowania, ktory ma jeszcze STARE wiersze
      scen (`save_scenes` wykonuje sie dopiero po udanym planowaniu). "Wznow"
      generuje wtedy nieaktualny plan bez nacisniecia "Generuj". Poprawka razem z
      punktem wyzej
- [ ] **Sredni:** "Ponow" na jednej scenie generuje tez inne sceny w `error`
      (~l. 462) — `reset_scene` czysci tylko `idx`, a `run_generation` bierze
      wszystko, co nie jest `done`. Placi sie za sceny, ktorych uzytkownik nie
      wybral, a scena odrzucona przez filtr tresci znow wywraca joba na `error`
- [ ] **Sredni:** timeout moze uszkodzic klip (`fal_provider.py`, `.part`).
      `wait_for` anuluje coroutine, nie watek `urlretrieve`; retry pisze do tego
      samego `{out_path}.part`, dwa watki trafiaja w jeden inode, a wynik ma status
      `done`. Poprawka: unikalna nazwa tymczasowa na probe (`{out_path}.{uuid}.part`)
- [ ] **Niski:** `/resume` (~l. 515) ustawia `uploaded` i czysci `error` PRZED
      sprawdzeniem limitu jobow — po 409 job traci komunikat bledu. Najpierw
      `_assert_slot_free`, jak w `retry_scene`

### 1.3 Czyszczenie danych — ZROBIONE (0.5.0)
- [x] Task kasujacy pliki (clips, uploads, frames) starsze niz `RETENTION_DAYS` (7)
      — petla asyncio w `lifespan`, nie cron: dziala tez lokalnie i nie wymaga
      instalacji na kazdym nowym serwerze. Pierwszy przebieg przy starcie
- [x] **`data/final/*.mp4` NIE ma retencji** — to produkt uzytkownika. Znika
      wylacznie przez `DELETE /jobs/{id}`
- [x] Sweep sierot: katalog roboczy bez wiersza w `jobs` (przerwany DELETE,
      reczne `rm` na serwerze). Prog wieku jest konieczny, bo `create_job`
      tworzy `uploads/{job_id}` przed INSERT-em
- [x] Kolumna `jobs.workdirs_purged_at` — bez niej „brak plikow" jest
      nieodroznialne od „job nigdy ich nie mial", a `Wznow` na starym jobie
      padal w tle jako goly `error`
- [x] Endpoint `DELETE /jobs/{id}` — usuwanie joba + plikow (baza przed dyskiem;
      resztki lapie sweep sierot). Przycisk „Usun" w „Moje filmy"
- [x] Limit jednoczesnych jobow per uzytkownik (`MAX_ACTIVE_JOBS_PER_USER`,
      domyslnie 3, prod 1) — liczony z `_running`, nie z bazy: dict czyta sie
      synchronicznie, `SELECT COUNT(*)` otwiera okno wyscigu na `await`

---

## Faza 2 — Autentykacja i konta uzytkownikow

Priorytet: KRYTYCZNY
Zaleznosci: brak

### 2.1 Rejestracja i logowanie — ZROBIONE (0.2.0)
- [x] Magic link (email) — najprostsze, bez hasel
- [ ] ~~Alternatywnie: Google OAuth~~ — niepotrzebne, magic link wystarcza
- [x] Tabela `users (id, email, created_at, last_login_at, is_active)`
- [x] Podpisane ciasteczko httpOnly — sesja
      (HMAC-SHA256 zamiast JWT: ten sam efekt, zero nowych zaleznosci)
- [x] ~~Middleware FastAPI~~ → `APIRouter(dependencies=[Depends(require_user)])`
      Middleware przechwycilby tez StaticFiles i trasy logowania, wiec wymagalby
      listy wyjatkow — a blad w takiej liscie to obejscie autoryzacji.

### 2.2 Powiazanie jobow z uzytkownikiem — ZROBIONE (0.2.0)
- [x] Kolumna `user_id` w tabeli `jobs`
- [x] Uzytkownik widzi tylko swoje joby (`get_owned_job`, 404 zamiast 403)
- [x] Endpoint `GET /jobs` — lista jobow danego uzytkownika
- [x] Strona "Moje filmy" — lista z miniaturkami, statusem, data
      (tylko do odczytu — wznawianie niedokonczonych jobow wymaga Fazy 1.1)

### 2.3 Prosty panel admina — ZROBIONE (0.6.0)
- [x] Endpoint `GET /admin/jobs` — joby wszystkich uzytkownikow, filtr po statusie,
      `LIMIT`/`OFFSET`. `LEFT JOIN users`, nie `JOIN`: joby sprzed 0.2.0 maja
      `user_id = NULL` i poza panelem nie widzi ich nikt
- [x] Podglad statusu, kosztu (szacunkowego), uzytkownika, liczby scen i bledu
- [x] Reczne usuwanie jobow — `DELETE /admin/jobs/{id}` przez wspolny
      `remove_job()` w `jobs.py`. Druga kopia tej logiki w `admin.py` rozjechalaby
      sie z regula „baza przed dyskiem" i z blokada 409 na jobie w trakcie pracy
- [x] Rola admina w `users.is_admin` (migracja w `_migrate()`) + `require_admin`.
      **403, nie 404** jak `get_owned_job`: id joba warto ukrywac, istnienia
      `/admin` nie — `app.js` i tak wysyla przycisk, ktory tam uderza
- [x] Zrodlem roli jest `ADMIN_EMAILS` z env, nie reczny UPDATE. `sync_admins()`
      przy starcie nadaje **i odbiera** flage (idempotentne — wynik zalezy tylko od
      env, nie od historii bazy); adres, ktory zarejestruje sie pozniej, dostaje
      flage w `get_or_create_user`
- [x] `GET /admin/users` + `POST /admin/users/{id}/active` — blokowanie kont.
      Kill-switch `is_active` istnial od 0.2.0, brakowalo mu tylko przycisku
- [ ] Audyt akcji admina — na razie `print()` do logow kontenera, bez tabeli
- [ ] Reczne wznowienie cudzego joba — wymaga decyzji, czyj slot w `_running`
      zajmuje admin (limit liczy sie per `user_id`)

---

## Faza 3 — Platnosci i system kredytowy

Priorytet: KRYTYCZNY
Zaleznosci: Faza 2 (konta uzytkownikow)

### 3.0 Uszczelnienie wyceny — ZROBIONE (0.7.0)
Warunek konieczny przed ledgerem: kredyty sa tylko tak wiarygodne, jak liczba,
ktora rezerwuja.
- [x] `duration_s` z allowlisty (`ALLOWED_DURATIONS`) w `/scenes/{idx}/update`
      **i** w `SceneItem` — `-100` dawalo ujemna rezerwacje, czyli doladowanie
- [x] `MAX_SCENES_PER_JOB` — `/scenes/sync` przyjmuje liste wprost od klienta
- [x] Nieznany model rzuca zamiast wyceniac sie po stawce Wana
      (`cost_per_second`), `create_job` odrzuca go z 400
- [x] `_sanitize_plan()` — prompt uzytkownika trafia do LLM doslownie, wiec plan
      nie jest zaufanym wejsciem
- [x] Jedna formula kosztu zamiast trzech (znikly kopie z `plan_scenes.py`
      i `app.js`; ta w JS liczyla z modelu wybranego w formularzu, nie z modelu joba)
- [x] `already_rendered()` → `job_state.is_rendered()` — wycena wznowienia musi
      dawac te sama odpowiedz, co pomijanie scen w generacji

### 3.1 System kredytow wewnetrznych — ZROBIONE (0.7.0)
- [x] Tabela `credits` (+ `job_id`, `ext_id UNIQUE`, `balance_after`)
  - `type`: `purchase` | `usage` | `refund` | `bonus`
- [x] Kolumna `credit_balance` w `users` — **autorytatywna**, `SUM(credits)` to
      audyt. Jedno wspoldzielone polaczenie bez transakcji sprawia, ze atomowe
      jest tylko pojedyncze zdanie SQL (`UPDATE ... WHERE credit_balance >= ?`
      + `RETURNING`), a `SELECT SUM` → `INSERT` to wyscig
- [x] 1 kredyt = 1 cent USD, `CREDIT_MARGIN` nad cennikiem fal.ai
- [x] Blokada generacji przy braku pokrycia — 402, nie 409
- [x] ~~Odejmowanie po ukonczeniu~~ → **rezerwacja przy starcie + zwrot roznicy**.
      Intencja („nie bierzemy za nieudane") zostaje, realizuje ja `refund`.
      Pobor po fakcie zostawialby kilkanascie minut generacji bez zadnej kontroli
      budzetu — trzy rownolegle joby wychodzily na minus
- [x] Refund przy bledzie — `settle_job()` w `finally` (`CancelledError` nie jest
      `Exception`, wiec `except` nie lapie zamkniecia kontenera) + sweep startowy
- [x] Kredyty powitalne `WELCOME_CREDITS`, idempotentne po `ext_id`

### 3.2 Doladowywanie kredytow — Stripe — ZROBIONE (0.7.0)
- [x] Stripe Checkout Session, pakiety $5 / $15 / $50 z bonusem za wiekszy
- [x] Webhook `checkout.session.completed`, podpis HMAC z surowych bajtow,
      idempotencja po `UNIQUE(ext_id)`. **Wlasny router bez `require_user`** —
      Stripe nie wysyla ciasteczka sesji
- [x] Bez SDK, na `httpx` — jak Resend; `pip install` to najciezszy moment deployu
- [x] `STRIPE_ENABLED=false` domyslnie: lokalny development bez konta Stripe
- [x] Ekran "Kredyty" + historia transakcji
- [ ] Faktury / paragony — Stripe pobiera platnosc, ale nic nie wystawia dokumentu

### 3.3 Ekran potwierdzenia kosztu — ZROBIONE (0.7.0)
- [x] "Ten film zuzyje X kredytow (masz Y)" na ekranie planu
- [x] Przycisk "Doladuj" **zamiast** "Generuj" przy niedoborze; 402 zostaje jako
      zabezpieczenie, bo saldo mogl w miedzyczasie zjesc inny job
- [x] Podsumowanie po generacji — zdarzenie SSE `credits` (zwrot + saldo)
- [ ] Saldo nie odswieza sie samo w innych kartach tej samej sesji

### 3.4 Otwarte po 0.7.0
- [ ] Rzeczywisty koszt u fal.ai przy retry to do 3x stawka sceny
      (`clip_max_retries = 2`), a uzytkownik placi 1x — pokrywa to marza,
      ale nikt tego nie mierzy
- [ ] Brak wygasania kredytow i zwrotu pieniedzy (tylko kredytow)
- [ ] Planowanie (koszt LLM) jest darmowe — do decyzji, czy ma takie zostac

---

## Faza 4 — Hosting i deploy

Priorytet: WYSOKI
Zaleznosci: Faza 1

### 4.1 Serwer produkcyjny — PRZYGOTOWANE (0.4.1), wdrozenie reczne
- [x] VPS — **Mikrus 2.1** (1 vCPU, ~1 GB RAM, ~10 GB dysku), nie Hetzner/DO.
      Duzo ciasniej niz zakladal pierwotny wpis, stad `SEMAPHORE_LIMIT` i swap
- [x] Osobny `docker-compose.prod.yml` (nie ten sam co lokalnie): bez bind-mounta
      `./app`, z `restart: unless-stopped`, healthcheckiem i limitem logow
- [x] Domena — subdomena z panelu Mikrusa wskazujaca na port 30108
- [x] ~~Caddy jako reverse proxy~~ — HTTPS terminuje proxy Mikrusa.
      Let's Encrypt wymagalby portow 80/443, ktorych na Mikrusie nie ma
- [x] Volume dla `/data` — persystentny miedzy deployami (bez zmian)
- [ ] Samo wdrozenie na serwerze — instrukcja: `docs/deployment.md`

### 4.2 Backup i monitoring
- [x] Backup SQLite co 24h — `scripts/backup-db.sh` (cron), `sqlite3.backup()`
      zamiast `cp`, rotacja 7 dni. **Kopia ladzie na tym samym dysku** — chroni
      przed uszkodzeniem bazy, nie przed utrata VPS-a
- [x] ~~Logi do pliku + rotacja (logrotate)~~ — `json-file` z `max-size: 10m`,
      `max-file: 3`. Logrotate nie jest potrzebny, docker rotuje sam
- [x] Healthcheck endpoint `GET /health` — sprawdza baze (`SELECT 1` z wlasnym
      timeoutem) i wolne miejsce w `data_dir`; 503 gdy ktorykolwiek zawiedzie
- [ ] Powiadomienie (email/Slack) gdy job upadnie

### 4.3 Deploy flow
- [ ] Skrypt `deploy.sh`: git pull → docker compose build → docker compose up -d
- [ ] Env vars na serwerze (nie w repo)

---

## Faza 5 — UX i funkcjonalnosc

Priorytet: SREDNI
Zaleznosci: Faza 1-3

### 5.1 Powiadomienia
- [ ] Email po ukonczeniu generacji z linkiem do pobrania
- [ ] Email po bledzie z mozliwoscia wznowienia
- [ ] Integracja: Resend lub SendGrid (prosty API)

### 5.2 Polepszenie UI
- [x] Reczny plan scen na ekranie wgrywania (karta na zdjecie, opis ruchu,
      dlugosc, lancuchowanie), planer AI jako opcja — ZROBIONE (0.8.0)
- [ ] Drag & drop zmiany kolejnosci scen
- [ ] Podglad klipu po wygenerowaniu (przed stitchem)
- [ ] Pasek postepu per scena — czesciowo w 1.2 (lista scen + zdarzenie SSE `scene`);
      zostaje sam pasek, ktory dalej stoi na sztywnych 50% przez cala generacje
- [ ] Responsywnosc na mobile
- [ ] Polskie znaki w UI (obecnie ASCII)

### 5.3 Opcje eksportu
- [ ] Wybor rozdzielczosci: 720p / 1080p
- [ ] Wybor aspect ratio: 16:9, 9:16 (vertical), 1:1
- [ ] Dodawanie sciezki audio (upload MP3 + FFmpeg mux)
- [ ] Watermark (opcjonalny, usuwany po zaplacie)

---

## Faza 6 — Skalowalnosc (po walidacji z klientami)

Priorytet: NISKI — tylko jesli bedzie ruch
Zaleznosci: Faza 4

### 6.1 Kolejka zadan
- [ ] Redis + Celery (lub arq) zamiast asyncio.create_task
- [ ] Oddzielny worker do generacji klipow
- [ ] Mozliwosc skalowania workerow horyzontalnie

### 6.2 Storage plikow
- [ ] S3-compatible (MinIO / Cloudflare R2) zamiast lokalnego dysku
- [ ] Signed URLs do pobierania filmow (wygasaja po 24h)
- [ ] CDN na finalne pliki

### 6.3 Baza danych
- [ ] Migracja SQLite → PostgreSQL
- [ ] Alembic do migracji schematu

---

## Kolejnosc pracy (sugerowana)

```
Faza 1 (stabilnosc)  ──►  Faza 2 (auth)  ──►  Faza 3 (platnosci)  ──►  Faza 4 (deploy)
                                                                              │
                                                                              ▼
                                                                    Faza 5 (UX) + Faza 6 (skala)
```

Fazy 1-4 = MVP gotowe do pokazania klientom.
Fazy 5-6 = iteracja po feedbacku.
