# Changelog

## [0.8.0] — 2026-09-18 — Reczny plan scen jako domyslny tryb

Planer LLM dostawal tylko liczbe zdjec i ogolny opis — nie widzial zdjec, wiec
`sub_prompt` rozjezdzaly sie z trescia klatek (sprawdzone na sekwencji 10 klatek
artroskopii kolana). Teraz uzytkownik od razu na ekranie wgrywania dostaje karte
sceny na kazde zdjecie; planer AI zostaje jako przelacznik i dziala jak wczesniej.

- **`POST /jobs` z `plan_mode=manual` i `scenes` (JSON)** tworzy job od razu w
  statusie `planned` — bez `/plan` i bez `run_planning`. Walidacja (model, tryb,
  `SceneItem`, zakres `image_index`, `_normalize_manual_scenes`) biegnie **przed**
  zapisem na dysk; `validate()` po zapisie plikow, a jej blad zostawia pliki dla
  orphan sweepa. Sceny sa zapisywane przed zmiana statusu: crash pomiedzy daje
  `uploaded` + sceny, co `_decide_stage` juz czyta jako `planned`
- **Nowa kolumna `jobs.plan_mode`** (`auto` | `manual`, stare joby `auto`).
  Decyduje, czym jest `jobs.prompt`: w `manual` to opcjonalny **styl** doklejany
  do kazdej sceny (`job_state.scene_prompt`), w `auto` opis filmu, ktorego sie
  nie dokleja. Blokuje tez LLM: `/plan` na jobie recznym to 409, a `/resume`
  recznego joba bez scen to 409 zamiast odpalenia planera — `run_planning`
  konczy sie `save_scenes` (DELETE+INSERT) i nadpisalby plan uzytkownika
- **Reguly planu recznego** (`_normalize_manual_scenes`): pierwsza scena z
  `chain_from_prev` i pusty opis to 400 z numerem sceny. Kontynuacja na koncu
  filmu jest poprawna i nie jest ruszana. Zdjecie, od ktorego nie startuje zadna
  scena, przechodzi — UI tylko ostrzega
- **Wspolny renderer kart scen** (`renderSceneCards` w `app.js`) dla szkicu na
  ekranie 1 i edytora na ekranie 2. Nowe na obu: checkbox „Kontynuuj poprzednia”
  i przycisk „+ kontynuacja”. Scena-kontynuacja pokazuje wyszarzona miniature i
  „start z ostatniej klatki sceny N”, bo pipeline ignoruje wtedy jej zdjecie.
  Zmiana lancuchowania w edytorze idzie przez `/scenes/sync` (zmienia grupowanie
  lancuchow), nie przez `/scenes/{idx}/update`
- `validate()` wydzielone na synchroniczne `process_images()` uruchamiane przez
  `asyncio.to_thread` — od 0.8.0 wolane prosto z handlera, gdzie Pillow
  blokowalby event loop (a z nim SSE innych jobow)
- Miniatury z `File` maja jeden object URL na plik (`WeakMap`), zamiast nowego
  `createObjectURL` przy kazdym renderze

## [0.7.0] — 2026-09-15 — System kredytowy i platnosci (roadmap Faza 3)

Do tej pory kazdy zalogowany uzytkownik wydawal pieniadze wlasciciela instancji,
a jedynym hamulcem byl `MAX_ACTIVE_JOBS_PER_USER`. 0.4.1 wpisywalo to jako znane
ryzyko wdrozenia, a `admin.py` przyznawal, ze kolumna „Koszt" w panelu to
szacunek plannera, nie wydatek. Teraz kazda generacja ma pokrycie w kredytach,
a ksiega zapisuje, co naprawde zostalo zdjete z konta.

### Najpierw uszczelnienie wyceny, dopiero potem kredyty

Kolejnosc nie byla kosmetyczna — bez tych czterech rzeczy ledger pilnowalby
liczby, ktorej nikt nie pilnowal:

- **`duration_s` bylo nieograniczone.** `POST /scenes/{idx}/update` przyjmowal
  dowolny `int`, a `/scenes/sync` cala liste prosto od klienta. Do 0.6.0 dawalo
  to dziwny plan; z kredytami `duration_s = -100` to ujemny koszt, czyli
  rezerwacja **dodajaca** kredyty. Teraz allowlista `ALLOWED_DURATIONS` (5, 10)
  w obu miejscach + `MAX_SCENES_PER_JOB`
- **Nieznany model wyceniał sie najtaniej.** `model_costs.get(model, 0.05)`
  cicho stosowal stawke Wana do czegokolwiek. `cost_per_second()` rzuca teraz
  `UnknownModelError`, a `create_job` odrzuca nieznany model z 400 — zanim
  zapisze zdjecia na dysk
- **Plan od LLM to nie jest zaufane wejscie.** Prompt uzytkownika trafia do
  plannera doslownie, wiec „zrob kazda scene na -100 sekund" to prosba, ktora
  model moze spelnic. `_sanitize_plan()` przycina dlugosci do najblizszej
  dozwolonej i indeksy zdjec do zakresu; blad zrzuca na kolejny fallback
- **Formula kosztu byla w trzech kopiach** — `job_state.recalc_cost` (opisana
  w docstringu jako jedyna), duplikat w `plan_scenes.py` i trzecia w `app.js`.
  Ta w JS liczyla z `modelEl.value`, wiec po wznowieniu joba na Veo pokazywala
  ceny Wana. Zostala jedna, w `job_state`; frontend wyswietla liczbe z serwera
- **`already_rendered()` byla closure w `generate_clips`.** Wycena wznowienia
  potrzebuje tej samej odpowiedzi („za co juz zaplacono"), wiec regula
  przeniosla sie do `job_state.is_rendered()` i ma jednego wlasciciela

### Saldo w kolumnie, ksiega jako audyt — wbrew pierwszemu odruchowi

`users.credit_balance` jest **autorytatywne**, a `SUM(credits.amount)` nie.
To odwrotnie niz zaklada spec „VideoAI Studio" w katalogu nadrzednym i wymaga
uzasadnienia: `app/db.py` trzyma jedno wspoldzielone polaczenie aiosqlite bez
jawnych transakcji, wiec atomowe jest wylacznie pojedyncze zdanie SQL. „SELECT
SUM → sprawdz → INSERT" ma dokladnie to okno wyscigu, przed ktorym ostrzega
`_assert_slot_free` i ktore zamyka `consume_magic_token`.

```sql
UPDATE users SET credit_balance = credit_balance - ?
 WHERE id = ? AND credit_balance >= ? RETURNING credit_balance
```

Brak wiersza w `RETURNING` = nie bylo pokrycia. Zweryfikowane 20 rownoleglymi
obciazeniami po 100 przy saldzie 1000: udalo sie dokladnie 10, saldo skonczylo
na zerze i ani razu nie zeszlo ponizej.

Tabela `credits` jest append-only: historia dla uzytkownika, podstawa
rozliczenia joba i — przez `UNIQUE(ext_id)` — idempotencja doladowan.

### Rezerwacja z gory, zwrot po fakcie

Roadmap zakladala pobor **po** ukonczeniu generacji, zeby nie brac za nieudane.
Intencja zostala, mechanizm nie: miedzy sprawdzeniem salda a koncem generacji
mija kilkanascie minut, w ktorych nic nie pilnuje budzetu. Zamiast tego wpis
`usage` na pelny szacunek przy starcie i `refund` za to, co nie powstalo.

Cala ksiegowosc joba wynika z wpisow z jego `job_id`, bez osobnego stanu
rezerwacji:

```
spent_net = -SUM(amount WHERE job_id = ?)
actual    = credits_for(model, sceny ze status='done')
refund    = spent_net - actual
```

Formula jest idempotentna, wiec `settle_job()` mozna wolac wielokrotnie — i sama
obsluguje retry oraz wznowienie, bo kazde z nich dokłada wlasny `usage`.
Sprawdzone: job na 6 scen, rezerwacja 600, restart w polowie, zwrot 600,
wznowienie, rezerwacja 600 ponownie, film gotowy — netto 600 za 6 scen.

### `settle_job` w `finally`, nie w `except`

`asyncio.CancelledError` dziedziczy z `BaseException`, wiec `except Exception`
w `run_generation` **nie lapie** zamkniecia kontenera — a to najczestszy moment,
w ktorym rezerwacja mialaby przepasc. W logach widac, ze zwrot wykonuje sie
miedzy „Shutting down" a startem nowego procesu, czyli wlasnie tam.

Drugi bezpiecznik: sweep startowy. `reconcile_interrupted()` zwraca teraz liste
id zamiast liczby wierszy, a `lifespan` rozlicza kazdy z nich. Idempotencja
sprawia, ze oba mechanizmy moga zadzialac na tym samym jobie bez kolizji.

Blad samego rozliczenia jest polykany i logowany: wyjatek z `finally`
przykrylby prawdziwy blad generacji i zamienil czytelna awarie w blad ksiegowy.

### Gdzie rezerwacja, a gdzie nie

| Sciezka | Kwota |
|---|---|
| `POST /jobs/{id}/generate` | wszystkie sceny bez gotowego klipu |
| `POST /jobs/{id}/resume` (stage `generating`) | tylko sceny nie-`is_rendered` |
| `POST /jobs/{id}/scenes/{idx}/retry` | ta jedna scena |
| `POST /jobs/{id}/plan` | brak — planowanie to ulamek centa u LLM |

Obciazenie idzie **w handlerze, przed `_start()`**, a nie w samym `_start()`,
mimo ze to jedyna brama kazdego taska: `_start` jest synchroniczne swiadomie,
a zapis do bazy wymaga `await`. Wyscigu i tak nie ma, bo `try_charge` rozstrzyga
wszystko jednym zdaniem SQL.

W `retry_scene` obciazenie stoi **przed** skasowaniem klipu — z tego samego
powodu, co sprawdzenie limitu jobow: odmowa po wyczyszczeniu sceny zostawilaby
ja skasowana i nic biegnacego, co by ja odtworzylo.

### Stripe bez SDK

`app/services/stripe_client.py` na `httpx`, jak `resend_sender.py`. Biblioteka
`stripe` ciagnie wlasne zaleznosci, a `pip install` jest najbardziej
pamieciozernym momentem deployu na 1 GB RAM.

- **Webhook ma wlasny router**, bez `require_user`: Stripe nie wysyla ciasteczka
  sesji, wiec trasa na routerze `jobs` konczylaby kazde doladowanie 401-ka.
  Uwierzytelnieniem jest podpis HMAC-SHA256 — repo mialo juz ten wzorzec
  w `auth/tokens.py`
- Podpis liczony z **surowych bajtow** (`await request.body()` przed jakimkolwiek
  `.json()`): ponowna serializacja zmienia biale znaki i nic sie nigdy nie zgadza
- Tolerancja 300 s na timestamp — bez niej przechwycone zadanie da sie odtwarzac
  bez konca
- Liczba kredytow z **wlasnego cennika po `metadata[package_id]`**, nigdy
  z `amount_total` w payloadzie. O wysokosci doladowania decyduje serwer
- Webhook zwraca 200 takze dla zdarzen nieobslugiwanych i dla duplikatu; kazdy
  inny kod Stripe traktuje jako awarie i ponawia przez kolejne doby. 400 zostaje
  wylacznie dla zlego podpisu
- `STRIPE_ENABLED=false` domyslnie, jak `MOCK_PROVIDER=true`: lokalny development
  nie moze wymagac konta u dostawcy platnosci

Zweryfikowane dziewiecioma przypadkami, w tym: to samo zdarzenie dwa razy (drugi
raz `duplicate`, saldo bez zmian), zly sekret, podpis sprzed 9999 s, podmienione
cialo przy poprawnym podpisie, sesja nieoplacona.

### Kredyty powitalne

`WELCOME_CREDITS` (500 = $5), przyznawane przez `sync_welcome_credits()`
idempotentnie po `ext_id = "welcome:<user_id>"`. Ta sama zasada co `sync_admins()`:
wynik zalezy od env, nie od historii bazy. Konta sprzed 0.7.0 lapie
`backfill_welcome_credits()` w `lifespan` — 9 kont przy pierwszym starcie,
drugi start nie dosypal.

Konsekwencja swiadoma: podniesienie `WELCOME_CREDITS` **nie** dosypuje roznicy
istniejacym kontom, bo wpis juz istnieje.

Migracja `users.credit_balance` nie robi backfillu (zero + osobna funkcja),
zeby schemat nie decydowal o polityce cenowej.

### Zaokraglenie, ktore kosztowalo 7%

`credit_rate()` liczy stawke z `model_costs` razy marza, a nie z drugiej tabeli
stawek — ta rozjechalaby sie z cennikiem fal.ai przy pierwszej jego zmianie.
Pierwsza wersja robila samo `ceil()` i wyceniala Klinga na 15 kr/s zamiast 14,
bo `0.07 * 100 * 2.0` to w zmiennoprzecinkowym `14.000000000000002`. Teraz
`ceil(round(x, 6))`: prawdziwe pol kredyta dalej idzie w gore, artefakt
reprezentacji nie.

Stawki przy `CREDIT_MARGIN=2.0`: Wan 10 kr/s, Kling 14, Veo 20.

### UI

- Saldo w topbarze (z `/auth/me`, ktore frontend i tak wola przy bootstrapie)
- Ekran planu: „Ten film zuzyje X kredytow (masz Y)". Gdy brakuje, `Generuj`
  **znika** na rzecz `Doladuj` — ten sam wzorzec, co ukrywanie `Wznow` przy
  `files_purged`: nie pokazuj przycisku, ktory serwer i tak odrzuci. 402 zostaje
  jako zabezpieczenie, bo saldo mogl w miedzyczasie zjesc inny job
- 402 na `/generate` **wraca na ekran planu**, nie zostawia uzytkownika na ekranie
  postepu z czerwonym bledem i niczym do klikniecia
- Siodmy ekran `screen-credits`: pakiety i historia. „Wstecz" wraca tam, skad
  sie przyszlo — wejscie jest i z topbaru, i z planu
- Nowe zdarzenie SSE `credits` z kwota zwrotu i saldem; emitowane z `finally`,
  wiec przychodzi takze po bledzie generacji
- `Wznow` jest wylaczony przy saldzie 0. Dokladny koszt dokonczenia zalezy od
  tego, ktore klipy leza na dysku, a lista jobow tego nie wie — zero jest jednak
  jednoznaczne. Przy saldzie dodatnim decyzje podejmuje serwer (402 z kwota)

### Panel admina

- `GET /admin/users` pokazuje saldo i **`credits_spent` z ksiegi** obok
  dotychczasowego `est_cost_usd`. Dwie liczby celowo: rozjezdzaja sie dokladnie
  tam, gdzie szacunek byl zly — nieudana scena, retry, job porzucony po planowaniu.
  Dwa osobne agregaty, nie jeden `SELECT` z dwoma `LEFT JOIN`: laczenie `jobs`
  i `credits` naraz mnozy wiersze i obie sumy wychodza zle
- `POST /admin/users/{id}/credits` — reczne doladowanie, jedyna droga przy
  `STRIPE_ENABLED=false`. Ujemna kwota dozwolona celowo: bez niej nie da sie
  cofnac pomylki ani zejsc kontem do zera przy testowaniu blokady. Bez `ext_id`,
  bo to jednorazowa decyzja czlowieka — dwa doladowania po 100 maja dac 200
- W tabeli kont: kolumny **Wydano (kr)** i **Saldo** oraz przycisk **Kredyty**
  pytajacy o kwote i powod (powod trafia do historii uzytkownika). Dostepny takze
  na wlasnym koncie admina — inaczej niz blokada, ktorej admin nie moze zalozyc
  sobie samemu, bo nie mialby jak jej cofnac. Po udanej zmianie przeladowywana
  jest cala tabela, nie jedna komorka: ujemna kwota rusza tez kolumne „Wydano"

### Czego tu nie ma

- **Faktur i VAT-u.** Stripe zbiera platnosc, nic nie wystawia dokumentu
- **Wygasania kredytow** ani zwrotu pieniedzy (tylko kredytow)
- Kredyty nie pokrywaja kosztu LLM przy planowaniu — jest darmowe
- Rzeczywisty koszt u fal.ai przy retry to do 3x stawka sceny
  (`clip_max_retries = 2`), a uzytkownik placi 1x. Marza ma to pokryc
- `PRAGMA foreign_keys` nadal wylaczone, wiec `credits.user_id REFERENCES users`
  jest dekoracyjne — jak reszta kluczy obcych w tym schemacie

## [0.6.0] — 2026-09-14 — Panel admina (roadmap Faza 2.3)

Pierwszy widok na dane wszystkich uzytkownikow. Do tej pory jedyna droga do
cudzego joba byla przez `sqlite3` na serwerze.

### Rola admina — env, nie reczny UPDATE
- `users.is_admin` (migracja w `_migrate()`; `CREATE TABLE IF NOT EXISTS` nie
  dodalby kolumny do istniejacej bazy). Bez backfillu: stale wartosci domyslne
  ALTER TABLE przyjmuje, wiec istniejace konta zaczynaja jako zwykli uzytkownicy
- `ADMIN_EMAILS` (lista po przecinku) → `sync_admins()` w `lifespan`, po `get_db()`,
  bo to `_migrate()` dodaje kolumne. Dwa UPDATE-y: nadaje flage adresom z listy i
  **odbiera** wszystkim pozostalym. Dzieki temu funkcja jest idempotentna — wynik
  zalezy wylacznie od env, nie od tego, co bylo w bazie. Wariant „tylko nadawaj"
  zalezalby od historii i po kilku restartach z roznymi `.env` baza pamietalaby
  ich sume
- Konsekwencja swiadoma: **puste `ADMIN_EMAILS` zostawia baze bez adminow**
  (SQLite dopuszcza `NOT IN ()` i pasuje ono do kazdego wiersza). Powrot to
  `UPDATE users SET is_admin=1` w `sqlite3` na serwerze
- Adres z listy, ktory zarejestruje sie **po** starcie, dostaje flage w
  `get_or_create_user` — `sync_admins()` widzi tylko konta istniejace przy starcie
- `get_current_user` czyta `is_admin` przy kazdym zadaniu, wiec usuniecie adresu z
  `ADMIN_EMAILS` + restart odcina dostep natychmiast. Ta sama wlasnosc co `is_active`

### `require_admin` — 403, nie 404
- `app/auth/deps.py`. Reszta API ukrywa cudze zasoby pod 404 (`get_owned_job`),
  zeby nie dalo sie zgadywac id jobow. Tu jest odwrotnie: `/admin` nie jest
  tajemnica — `app.js` wysyla przycisk, ktory tam uderza — a 403 latwiej debugowac
- Nowy router **nie dziedziczy** ochrony po `jobs`, stad
  `APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])`. Router
  wlaczony w `main.py` przed montowaniem `StaticFiles` na `/`, inaczej trasy
  zwracalyby 404 od static files bez zadnego bledu przy starcie

### Endpointy
- `GET /admin/jobs` — filtr `status`, `LIMIT`/`OFFSET`, liczba scen i laczny czas.
  `LEFT JOIN users`: joby sprzed 0.2.0 maja `user_id = NULL`, nie widzi ich zaden
  uzytkownik w „Moje filmy" i to jedyne miejsce, gdzie da sie do nich dotrzec
- `DELETE /admin/jobs/{id}` — ta sama sciezka co usuwanie wlasnego joba
- `GET /admin/users` — konta z liczba jobow i suma kosztow. **Koszt jest
  szacunkiem planera** (`est_cost_usd`), nie tym, co naliczyl fal.ai; realne wydatki
  pojawia sie dopiero z ledgerem w Fazie 3
- `POST /admin/users/{id}/active` — blokada/odblokowanie konta. Dziala od nastepnego
  zadania zablokowanego uzytkownika, a stary magic link go nie wskrzesi
  (`consume_magic_token` sprawdza `is_active = 1`). Joby juz uruchomione koncza sie
  normalnie: blokada zatrzymuje nowe wydatki, nie wyrzuca oplaconej w polowie generacji
- 409 na probe zablokowania **wlasnego** konta — zablokowany admin nie moze sie
  zalogowac, zeby to cofnac, wiec z UI nie byloby powrotu

### `remove_job()` wyciagniete z `delete_job`
- `app/api/jobs.py`. Endpoint uzytkownika i endpoint admina roznia sie tylko tym,
  jak znajduja joba; reszta — 409 gdy job jest w `_running` albo w statusie
  aktywnym, kolejnosc „wiersz przed plikami", `cleanup.purge_all()` — istnieje raz
- Funkcja zostaje w `jobs.py`, bo `_running` jest stanem modulowym tego pliku

### Panel w UI
- Szosty ekran (`screen-admin`) w tym samym `showScreen()`, dwie tabele: joby i
  konta. Przycisk „Admin" ukrywany po `is_admin` z `GET /auth/me` — to sama
  kosmetyka, kazda trasa `/admin` sprawdza role po stronie serwera

### Czego tu nie ma
- Audytu: kto skasowal czyj film widac tylko w `print()` w logach kontenera
- Wznawiania cudzych jobow — limit `MAX_ACTIVE_JOBS_PER_USER` liczy sloty per
  `user_id`, wiec admin wznawiajacy cudzy job zajmowalby slot tamtego uzytkownika

### Przy okazji (commit `cb0bb67`, bez osobnej wersji)
- `ProviderError(message, retryable)` w `providers/base.py` +
  `_translate_fal_error()` w `fal_provider.py`: 422 z fal.ai wklejal wczesniej caly
  request (z obrazem w base64) do `scenes.error`, logow i UI. Bledy walidacji, w tym
  `content_policy_violation`, nie sa ponawiane — identyczny request padnie identycznie

## [0.5.0] — 2026-08-30 — Czyszczenie danych (roadmap Faza 1.3)

Zamyka oba ryzyka wdrozeniowe wpisane w 0.4.1: brak czyszczenia plikow i brak
limitu jobow na uzytkownika.

### Retencja materialu roboczego
- `app/services/cleanup.py` — **jedyne miejsce w aplikacji, ktore kasuje pliki joba**.
  Sciezki bierze wylacznie z `job_state` (`uploads_dir`, `frames_dir`, `clips_dir`,
  `final_path`); wlasne budowanie sciezek rozjechaloby sie z tym, co pipeline
  naprawde zapisal, a skutkiem byloby ciche niesprzatanie polowy danych
- Petla `retention_loop` startuje w `lifespan`, pierwszy przebieg **od razu przy
  starcie** — restart kontenera jest pelnoprawnym sposobem wymuszenia sprzatania.
  Odpalana po `reconcile_interrupted()`, nie przed: sweep pomija joby w statusie
  aktywnym, a przed tym wywolaniem joby po martwym procesie wciaz taki nosza
- `RETENTION_DAYS` (7), `CLEANUP_INTERVAL_H` (24) — druga zmienna istnieje po to,
  zeby dalo sie to przetestowac bez czekania dobe

### Gotowe filmy nie maja retencji
- Kasowany jest **tylko material roboczy** (`uploads`, `frames`, `clips`).
  `data/final/*.mp4` zostaje bezterminowo i znika wylacznie przez `DELETE`.
  To swiadomy podzial: material roboczy jest kilka razy wiekszy i po tygodniu
  sluzy juz tylko do wznowienia joba, ktorego nikt nie wznowi — a film jest
  produktem, za ktory ktos zaplacil
- Konsekwencja: dysk **dalej rosnie**, wolniej. `/health` zostaje jako ostrzezenie

### Kolumna `jobs.workdirs_purged_at`
- Migracja w `_migrate()`; stare wiersze zostaja `NULL`, czyli „jeszcze nie sprzatany"
- Nie jest optymalizacja. `os.path.exists() == False` jest wieloznaczne — „skasowane
  przez retencje" / „nigdy nie bylo" / „wolumin sie nie zamontowal" wymagaja innej
  odpowiedzi. Bez tego rozroznienia `Wznow` na starym jobie przechodzil przez
  `_decide_stage()` → `generating` i padal w tle jako goly `error`
- `_assert_files_present()` odmawia z 409 w `plan`, `generate`, `resume`, `retry`.
  Podglad listy scen dziala dalej (`renderSceneProgress` nie laduje obrazkow)

### Sweep sierot
- Katalog roboczy, ktorego `job_id` nie ma juz w tabeli `jobs` — skutek `DELETE`
  przerwanego miedzy baza a dyskiem albo recznego `rm` na serwerze
- **Prog wieku jest tu konieczny, nie ostrozny**: `create_job` tworzy
  `uploads/{job_id}` PRZED INSERT-em, wiec job w trakcie wgrywania zdjec przez
  chwile wyglada dokladnie jak sierota

### `DELETE /jobs/{id}`
- `Depends(get_owned_job)`, wiec cudzy job to **404, nie 403** — jak reszta API
- 409 gdy job jest w `_running` albo w statusie aktywnym
- **Kolejnosc: baza przed dyskiem.** Obie kolejnosci maja tryb awarii przy
  padnieciu w polowie, ale nierowny: osierocone pliki sa niewidoczne i lapie je
  sweep, a osierocony wiersz jest widoczny w „Moje filmy" i daje uzytkownikowi
  przyciski, ktore moga tylko zawiesc
- UI: przycisk „Usun" (ukryty w trakcie pracy joba), `confirm()`, usuniecie samej
  karty zamiast przeladowania listy — przeladowanie przewijaloby na gore

### Limit jednoczesnych jobow
- `MAX_ACTIVE_JOBS_PER_USER` (3; produkcyjne `.env` ustawia 1)
- `_running: set[str]` → `dict[str, str]` (`job_id` → `user_id`). Ta sama
  struktura odpowiada na oba pytania API: „czy ten job juz chodzi?" i „ile jobow
  tego uzytkownika chodzi?"
- Licznik z `_running`, **nie** `SELECT COUNT(*)` po statusach: dict czyta sie i
  zapisuje synchronicznie, wiec dwa rownolegle requesty nie przepleta sie miedzy
  sprawdzeniem a wpisem. Zapytanie do bazy w tym samym miejscu robi `await` i
  otwiera dokladnie to okno — podwojny klik przepuszczalby job ponad limit
- Sprawdzenie w `_start()`, czyli w jedynej bramie kazdego taska (plan / generate
  / resume / retry) — trasa dodana pozniej jest objeta limitem za darmo
- `retry_scene` sprawdza limit **przed** skasowaniem klipu; odmowa po skasowaniu
  zostawilaby scene wyczyszczona i nic, co ja odtworzy

### Kolizja nazw w stitchu (znalezione przy sprzataniu)

- `stitch_clips` pisalo pliki robocze prosto do `data/final/`: `normalized/norm_{i}.mp4`
  i `concat.txt` — **nazwy bez `job_id`**. Dwa joby stitchujace jednoczesnie
  nadpisywaly sobie te same pliki i film wychodzil posklejany z obu, bez zadnego
  bledu. `SEMAPHORE_LIMIT=1` przed tym nie chronil: ogranicza sceny w obrebie
  jednego joba, a nie dwoch uzytkownikow generujacych rownolegle
- Drugi skutek: `data/final` jest jedynym katalogiem, ktorego retencja nie moze
  ruszac po wieku (kazdy plik ma tam wlasciciela), wiec te smieci byly nieusuwalne
  — `norm_10.mp4` z 27 lipca lezal tam do dzis
- Teraz `data/stitch/{job_id}/`, kasowany w `finally` po ffmpegu (takze po bledzie:
  stderr jest juz w wyjatku, a retry i tak normalizuje od nowa). Katalog dolaczyl
  do `WORK_SUBDIRS`, wiec sweep jest zapasem na wypadek smierci procesu w trakcie
- `stitch_clips` dostaje `work_dir` od wolajacego, nie buduje go sam — ta sama
  zasada, co `out_path` u providerow: sciezki wybiera ten, kto zna `job_id`
- Zweryfikowane dwoma rownoleglymi jobami roznych uzytkownikow na rozlacznych
  kolorach: kazdy film zawiera wylacznie wlasne klatki

### Porzadki bazy

- Usuniete 11 jobow z `user_id IS NULL` (jeden batch z 2026-08-01, sprzed
  wprowadzenia kont w 0.2.0). Byly **juz niewidoczne** dla kazdego uzytkownika,
  bo `WHERE user_id = ?` nigdy ich nie lapalo — wiec nikt nic nie stracil
- Skasowane przez `cleanup.purge_all()`, nie samym SQL-em: `data/final` nie
  podlega ani retencji, ani sweepowi sierot, wiec 6 filmow (17,8 MB) zostaloby
  trwalymi smieciami
- Praktyczny skutek: `jobs.user_id` nie ma juz zadnego NULL-a, a `create_job`
  nie potrafi go stworzyc — kod czytajacy `job["user_id"]` moze traktowac je
  jako obecne. Kopia sprzed operacji: `data/video_gen.db.pre-purge`

### Porzadki przy okazji
- `frames_dir()` i `final_path()` przeniesione do `job_state` — byly budowane
  inline w `generate_clips` i `stitch`, a cleanup bylby trzecim miejscem z wlasna
  kopia tej samej sciezki
- Indeks `idx_jobs_purge(workdirs_purged_at, created_at)` — bez niego sweep to
  full scan po calej tabeli co dobe

## [0.4.1] — 2026-08-29 — Przygotowanie do deployu na VPS (roadmap Faza 4)

Cel: uruchomienie na Mikrusie 2.1 (1 vCPU, ~1 GB RAM, ~10 GB dysku) pod publicznym
HTTPS. Maszyna jest znaczaco mniejsza niz zakladala Faza 4.1, wiec wiekszosc zmian
to nie kosmetyka deployowa, tylko zejscie z zasobami.

### Osobny compose produkcyjny
- `docker-compose.prod.yml` — port `30108:8000` (Mikrus przekierowuje
  `bob108.mikrus.xyz:30108` na `192.168.1.108:30108`)
- **Bez bind-mounta `./app`.** Lokalnie kontener czyta kod z dysku hosta; na serwerze
  znaczyloby to, ze niedokonczony `git pull` albo edycja w edytorze natychmiast trafia
  do zywej aplikacji. Bez mounta jedyna droga zmiany kodu jest `--build`
- `restart: unless-stopped` (powrot po reboocie i po OOM-killu), `mem_limit: 700m`
  (pod LXC bywa ignorowany — `docker inspect` pokazuje wtedy `0`), logi `json-file`
  ograniczone do 3 × 10 MB, zeby nie zjadly dysku dzielonego z plikami wideo
- `--proxy-headers` w uvicornie — za proxy Mikrusa request przychodzi po http

### Endpoint `/health`
- Sprawdza dwie rzeczy, ktore moga byc zepsute przy zywym procesie uvicorna: baze
  (`SELECT 1`) i wolne miejsce w `data_dir` (prog `MIN_FREE_DISK_MB = 500`)
- Odpyt bazy ma **wlasny** timeout 3 s, krotszy niz `timeout: 10s` healthchecku —
  SQLite w jednym procesie potrafi sie zablokowac pod obciazeniem ffmpeg, a
  healthcheck ma wtedy dostac czytelne `db: timeout`, nie zostac urwany przez dockera
- Zwraca **503**, nie 200 z polem `"unhealthy"`: healthcheck w compose patrzy tylko na
  to, czy `urlopen()` rzucil. Cialo odpowiedzi niesie oba pomiary, zeby diagnoza nie
  wymagala wchodzenia na serwer
- Zarejestrowany **przed** mountem `StaticFiles` — `Mount("/")` przechwycilby `/health`
  i zwrocil 404 statycznego pliku, bez zadnego bledu przy starcie
- Docker sam nie restartuje kontenera `unhealthy` (robi to tylko swarm), wiec falszywy
  alarm nie kladzie aplikacji — to obniza koszt sprawdzania wiecej niz mniej

### `SEMAPHORE_LIMIT` z env
- `settings.semaphore_limit` bylo zaszyte na 4. Na 1 vCPU to 4 rownolegle lancuchy
  scen, czyli 4 procesy ffmpeg naraz — prosta droga do OOM. Produkcyjne `.env` ustawia 1

### Odchudzony obraz
- `langgraph` i `langgraph-checkpoint-sqlite` usuniete z `requirements.txt` — **nic ich
  nie importowalo** (pipeline nigdy nie byl na LangGraph, mimo nazwy `app/graph/`).
  Ciagnely za soba `langchain-core`, `orjson`, `msgpack`. `pip install` jest
  najbardziej pamieciozernym momentem calego deployu i na 1 GB bez swapu potrafi
  zostac ubity w polowie, wiec to czesc wdrozenia, a nie porzadki obok

### Backup i dokumentacja
- `scripts/backup-db.sh` — `sqlite3.backup()` zamiast `cp` (baza jest otwarta przez
  dzialajacy kontener, zwykla kopia moze zlapac plik w polowie transakcji), rotacja
  7 dni, do crona
- `docs/deployment.md` — instrukcja krok po kroku, w tym plan B (`docker save | ssh
  docker load`), gdy build nie miesci sie w pamieci VPS-a
- `.env.prod.example` — szablon env na serwer

### Znane ryzyko wdrozenia
- **Czyszczenie plikow dalej nie istnieje** (Faza 1.3). 10 GB dysku dzielone z systemem
  przy braku cleanupu to kwestia tygodni. `docs/deployment.md` §8 opisuje reczne
  sprzatanie, `/health` ostrzega przed przekroczeniem progu
- Brak systemu kredytow i limitu jobow na uzytkownika (Faza 3, 1.3): kazdy zalogowany
  wydaje pieniadze wlasciciela instancji

## [0.4.0] — 2026-08-29 — Obsluga bledow i retry (roadmap Faza 1.2)

Po 0.3.0 job przezywal restart kontenera, ale **jedna nieudana scena dalej kladla cala
robote**: `asyncio.gather` bez `return_exceptions` przerywal oczekiwanie na pierwszym
wyjatku, tresc bledu szla tylko do loga (tabela `scenes` nie miala na nia kolumny),
a braku timeoutu nie ratowalo nic — scena, na ktora fal.ai nigdy nie odpowie, wieszala
joba na zawsze. Teraz nieudana scena to nieudana scena, a nie koniec filmu.

### Wyciek kolejek SSE (najpierw, bo retry by go zwielokrotnil)
- `get_event_queue()` dopisywalo kolejke do `_event_queues[job_id]` przy kazdym
  polaczeniu i **nigdy jej nie usuwalo**. `EventSource` wznawia polaczenie sam, wiec
  kazde odswiezenie karty dokladalo komplet nowych kolejek, a `_emit()` karmilo je dalej
- `release_event_queue()` wolane w `finally` generatora w `job_events` — rozlaczenie
  klienta anuluje generator, wiec `finally` jest jedynym miejscem, ktore na pewno zadziala.
  Klucz `job_id` znika po ostatnim subskrybencie
- Kolejka ma `maxsize=100`; przepelnienie znaczy "klient przestal czytac", wiec `_emit()`
  ja wyrejestrowuje zamiast pomijac zdarzenie. Pominiecie zostawiloby kolejke pelna na
  zawsze, a EventSource i tak polaczy sie ponownie

### Blad zapisany przy scenie
- Nowa kolumna `scenes.error` (migracja w `_migrate()`); `mark_scene_error` zapisuje do
  niej komunikat, `load_state` go czyta, a `mark_scene_done` czysci (`error = NULL`) —
  scena udana przy ponowieniu nie moze zostac z komunikatem z nieudanej proby
- To samo pietro wyzej: kazde przejscie `_update_job_status` na status inny niz `error`
  czysci `jobs.error`. Bez tego ukonczony film pokazywal w UI blad z poprzedniego biegu

### Czesciowa porazka zamiast calkowitej
- `asyncio.gather(..., return_exceptions=True)` — lancuchy dobiegaja do konca niezaleznie
  od siebie. Kazdy klip, ktory sie wygeneruje, to klip, za ktory nie placi sie drugi raz
- `generate_clips` nie orzeka juz o losie joba: zwraca `status`, `failed_scenes`
  i loguje `N/M scen nieudanych`. Decyzje podejmuje `run_generation`
- **`run_generation` sklada film tylko przy komplecie scen.** To nie kosmetyka:
  `clip_paths` wraca z wyciszonymi dziurami (`[p for p in clip_paths if p is not None]`),
  wiec jedna brakujaca scena przesunelaby liste wzgledem `chain_flags` i `stitch_clips`
  zrobilby przejscia miedzy zlymi parami. Do 0.3.0 ratowal nas tylko wyjatek

### Timeout
- `CLIP_TIMEOUT_S` (domyslnie 600) i `asyncio.wait_for` wokol wywolania providera,
  **wewnatrz** petli retry — timeout zachowuje sie jak kazda inna nieudana proba
- Plaski, nie skalowany do `duration_s`: wiekszosc czekania to kolejka po slot GPU
  u dostawcy, ktorej dlugosc klipu nie obchodzi. Za ciasny timeout to najdrozszy blad —
  dostawca liczy za rozpoczeta generacje niezaleznie od tego, czy odbierzemy plik
- `str(asyncio.TimeoutError())` to pusty string, wiec komunikat nazywa timeout wprost
  ("przekroczono limit 600s") zamiast urywac sie po dwukropku
- Znane ograniczenie: `FalProvider` pobiera plik przez `run_in_executor`, a anulowanie
  `wait_for` nie zabija watku w puli — pobieranie dokonczy sie i zostawi osierocony
  `{out_path}.part` (sprzata Faza 1.3)

### Ponowienie pojedynczej sceny
- `POST /jobs/{id}/scenes/{idx}/retry` — czysci wiersz sceny i jej plik, po czym
  uruchamia **zwykle** `run_generation`. Zadnej osobnej sciezki generacji: `already_rendered`
  z 0.3.0 pomija wszystkie pozostale sceny, wiec uzupelniana jest dokladnie jedna dziura
- Scena w srodku lancucha nie wymaga kaskadowego czyszczenia — `process_chain` jest
  sekwencyjny, wiec sceny po nieudanej nigdy sie nie wygenerowaly i sa `pending`
- Bramka jest **allowlista** (`RETRYABLE_JOB_STATUSES = ("error", "interrupted")`):
  endpoint wydaje pieniadze, wiec status dodany w przyszlosci jest odrzucany, dopoki
  ktos swiadomie go nie dopusci. Scena musi byc w `error`
- Guard `_running` sprawdzany **przed** wyczyszczeniem sceny; odmowa po wyczyszczeniu
  zostawilaby scene skasowana i nic biegnacego, co by ja odtworzylo

### UI
- Lista scen na ekranie postepu: numer, prompt, badge statusu, komunikat bledu i przycisk
  **"Ponow"** przy scenach, ktore padly. Zdarzenie SSE `scene` (emitowane od 0.3.0
  i do tej pory przez nikogo nie sluchane) aktualizuje wiersze na biezaco
- Przycisk **"Podglad"** na karcie w "Moje filmy" — otwiera ekran postepu **bez
  uruchamiania czegokolwiek**. Bez niego "Ponow" byl osiagalny wylacznie wtedy, gdy
  uzytkownik siedzial na ekranie w chwili awarii: "Wznow" natychmiast regeneruje
  wszystkie nieudane sceny naraz
- `showError()` przeladowuje liste z serwera — sceny z ogona zerwanego lancucha nigdy
  nie ruszyly, wiec nie emituja wlasnego zdarzenia i zostalyby na "Oczekuje"

### Znane braki (opisane w roadmapie 1.2)
- Wiersz sceny skacze z "Oczekuje" na "Gotowe" — nie ma zdarzenia na START sceny
- Dlugi `sub_prompt` jest obcinany w wierszu (`white-space: nowrap`)

## [0.3.0] — 2026-08-26 — Persystencja i wznawianie jobow (roadmap Faza 1.1)

Stan jobu w locie zyl w module-level diccie `_job_states`. Restart kontenera kasowal go
w calosci: job zostawal w bazie w statusie `generating` na zawsze, `POST /generate`
odpowiadal `400 "Job state not found"`, a uzytkownik zaczynal od zera — **placac fal.ai
drugi raz za klipy, ktore juz lezaly na dysku**. Teraz baza jest jedynym zrodlem prawdy.

### Baza jako zrodlo prawdy
- Nowa kolumna `scenes.image_index` (migracja w `_migrate()` + backfill ze starego
  `image_path = "image_3"`), indeks `idx_scenes_job_idx ON scenes(job_id, idx)`
- Nowy modul `app/services/job_state.py` — jedyna granica DB ⇄ dict stanu pipeline'u.
  Wezly (`validate`/`plan_scenes`/`generate_clips`/`stitch`) nie zmienily sygnatur
- `image_paths` odtwarzane z `data/uploads/{job_id}/`, nie z kolumny — system plikow
  i tak jest autorytetem, a kopia w bazie moglaby sie rozjechac po czyszczeniu (Faza 1.3)
- `generate_clips` zapisuje kazda ukonczona scene **od razu** (callbacki `on_scene_done`
  / `on_scene_error`), a nie po calym wezle — crash na scenie 5 zostawia sceny 0-4 zapisane
- Przy okazji per-scenowy postep w SSE (zdarzenie `scene` z `done`/`total`)

### Deterministyczne sciezki klipow
- `VideoProvider.generate_clip` dostal argument `out_path` — providery nie losuja juz
  wlasnych nazw `uuid4().hex`, przez ktore po restarcie nie dalo sie powiazac pliku ze scena
- Konwencja: `data/clips/{job_id}/scene_{idx:03d}.mp4`. Retry nadpisuje w miejscu,
  Faza 1.3 skasuje jeden katalog na joba
- `FalProvider` pobiera film na `{out_path}.part` i robi `os.replace()` — istnienie
  `out_path` oznacza "scena gotowa", wiec polowicznie pobrany plik nie moze tam trafic

### Wznawianie
- `reconcile_interrupted()` w `lifespan`: statusy `planning`/`generating`/`stitching`
  to po restarcie z definicji trup → nowy status `interrupted`
- `POST /jobs/{id}/resume` — zwraca `stage` (`planning`/`planned`/`generating`/`done`).
  Job w statusie `planned` **nigdy** nie startuje generacji sam: to klikniecie "Generuj"
  wydaje pieniadze. Blad w pojedynczej scenie wznawia sie jako `generating`, nie
  `planning` — przeplanowanie skasowaloby wiersze `scenes` razem z gotowymi klipami
- `already_rendered()` pomija scene, ktora ma `status='done'`, plik pod `out_path`
  i niezerowy rozmiar. Baza mowi "done", a pliku nie ma → regeneracja (dysk ma ostatnie slowo)
- Guard `_running: set[str]` przeciw podwojnemu startowi. Utrata przy restarcie jest tu
  poprawna: po restarcie faktycznie nic nie biegnie
- Edycja planu odrzucana (409), gdy job jest w `generating`/`stitching`/`done` **albo**
  gdy ktorakolwiek scena ma `status='done'` — `save_scenes` robi DELETE+INSERT i skasowalaby
  `clip_path` oplaconych klipow

### UI
- Status `interrupted` ("Przerwane", bursztynowy badge) + przycisk "Wznow" na karcie
  w "Moje filmy"
- Edytor planu odtwarza sie po restarcie: miniatury z `GET /jobs/{id}/images/{index}`,
  lista w selectcie "Zdjecie" z `image_count` w `GET /jobs/{id}` (przegladarka nie ma
  juz obiektow `File`)

### Inne
- `PYTHONUNBUFFERED=1` w `docker-compose.yml` — bez tego `print()` z pipeline'u siedzial
  w buforze i nigdy nie trafial do `docker compose logs`
- `MockProvider` przestal sie wywalac na promptach z `:` / `%` / `'` — `_escape_drawtext()`.
  FFmpeg rozpakowuje filtergraph dwuprzebiegowo, wiec apostrofy same nie chronia dwukropka
  (ani backslash sam); potrzebne sa oba naraz

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
