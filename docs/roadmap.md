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
- [ ] Endpoint `POST /jobs/{id}/scenes/{idx}/retry` — ponowna generacja pojedynczej sceny
- [ ] Przycisk "Ponow" w UI przy scenie ze statusem error
- [ ] Timeout na generacje klipu (np. 10 min) — jesli fal.ai nie odpowie, oznacz scene jako error
- [ ] Globalny retry joba — przycisk "Wznow" jesli job upadl
- [ ] Wyciek kolejek SSE — `get_event_queue()` (`app/graph/pipeline.py`) dopisuje kolejke
      do `_event_queues[job_id]` przy kazdym polaczeniu i nigdy jej nie usuwa. Po
      rozlaczeniu klienta generator w `job_events` konczy sie, ale `_emit()` dalej robi
      `put_nowait` do osieroconej kolejki — rosnie i lista kolejek, i kazda kolejka
      z osobna. Klucze `job_id` tez nigdy nie znikaja ze slownika.
      Uwaga: `EventSource` wznawia polaczenie sam, wiec kazdy restart kontenera i kazde
      odswiezenie karty dokladaja komplet nowych kolejek do tych samych jobow.
      Fix: wyrejestrowanie kolejki w `finally` generatora, usuniecie klucza gdy lista
      pusta, plus `maxsize` na kolejce jako bezpiecznik na wypadek przeoczonej sciezki.
      Wazne przy retry: kazde "Ponow" to kolejna subskrypcja SSE, wiec bez tego fixa
      Faza 1.2 zwielokrotni problem zamiast go ujawnic.

### 1.3 Czyszczenie danych
- [ ] Task/cron kasujacy pliki (clips, uploads, frames) starsze niz 7 dni
- [ ] Endpoint `DELETE /jobs/{id}` — usuwanie joba + plikow
- [ ] Limit jednoczesnych jobow per uzytkownik (np. 3)

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

### 2.3 Prosty panel admina
- [ ] Endpoint `GET /admin/jobs` — lista wszystkich jobow (tylko admin)
- [ ] Podglad statusu, kosztu, uzytkownika
- [ ] Mozliwosc recznego usuwania jobow
- [ ] Rola admina w tabeli users (`is_admin`)

---

## Faza 3 — Platnosci i system kredytowy

Priorytet: KRYTYCZNY
Zaleznosci: Faza 2 (konta uzytkownikow)

### 3.1 System kredytow wewnetrznych
- [ ] Tabela `credits (id, user_id, amount, type, description, created_at)`
  - `type`: `purchase` | `usage` | `refund` | `bonus`
- [ ] Kolumna `credit_balance` w tabeli `users`
- [ ] Koszt joba = suma sekund * stawka modelu (juz wyliczane)
- [ ] Blokada generacji jesli brak kredytow
- [ ] Odejmowanie kredytow po ukonczeniu generacji (nie przed — zeby nie brac za nieudane)
- [ ] Refund kredytow przy bledzie generacji

### 3.2 Doladowywanie kredytow — Stripe
- [ ] Stripe Checkout Session — pakiety kredytow:
  - np. $5 / $15 / $50 (z bonusem za wiekszy pakiet)
- [ ] Webhook `checkout.session.completed` — dodanie kredytow do konta
- [ ] Strona "Doladuj kredyty" w UI
- [ ] Historia transakcji (zakupy + uzycia)

### 3.3 Ekran potwierdzenia kosztu
- [ ] Na ekranie planu: "Ten film zuzyje X kredytow (masz Y)"
- [ ] Jesli za malo — przycisk "Doladuj" zamiast "Generuj"
- [ ] Po generacji — podsumowanie: ile zuzyto, ile zostalo

---

## Faza 4 — Hosting i deploy

Priorytet: WYSOKI
Zaleznosci: Faza 1

### 4.1 Serwer produkcyjny
- [ ] VPS (Hetzner/DigitalOcean) — min. 2 vCPU, 4 GB RAM, 80 GB SSD
- [ ] Docker Compose na serwerze (ten sam co teraz)
- [ ] Domena + DNS
- [ ] Caddy jako reverse proxy (automatyczny HTTPS)
- [ ] Volume dla `/data` — persystentny miedzy deployami

### 4.2 Backup i monitoring
- [ ] Backup SQLite co 24h (kopia na S3 lub osobny dysk)
- [ ] Logi do pliku + rotacja (logrotate)
- [ ] Healthcheck endpoint `GET /health`
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
- [ ] Drag & drop zmiany kolejnosci scen
- [ ] Podglad klipu po wygenerowaniu (przed stitchem)
- [ ] Pasek postepu per scena (SSE z info ktora scena sie generuje)
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
