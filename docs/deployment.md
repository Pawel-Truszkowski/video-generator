# Deploy na VPS (Mikrus 2.1)

Cel: aplikacja dostepna publicznie po HTTPS, wystawiona z portu **30108** VPS-a
(`192.168.1.108:30108` → `bob108.mikrus.xyz:30108`), z sesjami na bezpiecznym
ciasteczku i bez developerskiego backdoora logowania.

Konfiguracja startowa: `MOCK_PROVIDER=true` — pelny flow (upload → plan → generacja
→ sklejenie) dziala bez wydawania pieniedzy. Przelaczenie na realne fal.ai to
sekcja [9](#9-przelaczenie-na-realnego-providera).

Zalozenia sprzetowe: 1 vCPU, ~1 GB RAM, ~10 GB dysku. To ma wplyw na kilka
decyzji nizej i nie sa one kosmetyczne.

---

## 0. Co masz i czego jeszcze potrzebujesz

| Rzecz | Wartosc |
|---|---|
| Port publiczny | `bob108.mikrus.xyz:30108` → `192.168.1.108:30108` |
| IPv6 VPS-a | `2a01:4f9:3090:1dcc::108` |
| Subdomena z HTTPS (panel Mikrusa → port 30108) | `https://ai-video-generator.cytr.us` |
| Katalog na VPS (przyjety w tej instrukcji) | `/opt/video-generator` |

Do zdobycia przed startem:

1. ~~**Subdomena z HTTPS**~~ — ZROBIONE. Panel Mikrusa, sekcja z domenami/subdomenami. Podpinasz
   darmowa subdomene (Mikrus daje m.in. `*.cytr.us`, `*.wykr.es`, `*.bieda.it`)
   do portu **30108**. Mikrus terminuje TLS
   i przekazuje ruch po http na twoj port. Nazwy zakladek w panelu bywaja
   zmieniane — szukaj opcji laczacej subdomene z portem TCP.
2. **Klucz Resend** — [resend.com](https://resend.com), darmowy plan. Bez niego
   aplikacja **nie wstanie** (patrz sekcja 4).
3. **Dostep SSH do VPS-a** — dane z panelu Mikrusa.

> **Dlaczego HTTPS nie jest opcjonalny.** `_check_config()` w `app/main.py` blokuje
> `MAIL_PROVIDER=console` tylko wtedy, gdy `COOKIE_SECURE=true`. Na golym
> `http://bob108.mikrus.xyz:30108` `COOKIE_SECURE` musi byc `false`, wiec ten
> strażnik milczy — a `POST /auth/request-login` zwraca wtedy link logujacy
> **w odpowiedzi HTTP dla dowolnego adresu e-mail**. Kazdy, kto zna URL, loguje
> sie na dowolne konto. HTTPS wlacza ochrone, ktora na http nie istnieje.

### Dlaczego NIE ma tu nginxa ani Caddy'ego

Reverse proxy w tym wdrozeniu juz jest — stoi u Mikrusa, przed twoim VPS-em.
To ono terminuje TLS dla `ai-video-generator.cytr.us` i przekazuje ruch po http
na port 30108. Postawienie wlasnego nginxa dolozyloby **drugi** proxy za pierwszym.

Klasyczne powody, dla ktorych stawia sie nginxa, tutaj nie wystepuja:

| Rola nginxa | Kto ja pelni w tym wdrozeniu |
|---|---|
| Terminacja TLS / certyfikat | proxy Mikrusa (Let's Encrypt wymagalby portow 80/443, ktorych nie masz) |
| Serwowanie statykow | `StaticFiles` w FastAPI — kilka plikow, ruch znikomy |
| Load balancing | jeden kontener, nie ma czego balansowac |
| Limit rozmiaru uploadu | walidacja w aplikacji (`max_upload_mb`) |

Doszedlby za to koszt: kolejny proces na maszynie z 1 GB RAM i — powazniej —
**nowa klasa bledow wokol SSE**. Nginx domyslnie ma `proxy_buffering on`, co
zbiera odpowiedz w buforze przed oddaniem klientowi. Dla `/jobs/{id}/events`
znaczy to, ze pasek postepu stanie w miejscu, choc generacja idzie. Naprawa
wymaga `proxy_buffering off`, `proxy_read_timeout` liczonego w minutach i
naglowka `X-Accel-Buffering: no`. Czyli nginx nie rozwiazuje tu zadnego problemu,
za to tworzy jeden, ktorego dzis nie masz.

**Kiedy wrocic do tego tematu:** wlasna domena zamiast subdomeny Mikrusa,
druga aplikacja na tym samym VPS-ie, albo rate limiting przed uploadem.

---

## 1. Przygotowanie VPS-a

```bash
ssh root@srvXX.mikr.us -p <port_ssh_z_panelu>
```

Docker (jesli nie ma):

```bash
apt update && apt install -y docker.io docker-compose-v2 git
systemctl enable --now docker
docker compose version   # musi odpowiedziec; jesli nie, uzywaj `docker-compose`
```

Sprawdz, z czym naprawde pracujesz:

```bash
free -m          # RAM + czy jest swap
df -h /          # wolne miejsce
nproc            # liczba rdzeni
```

**Jesli `free -m` pokazuje 0 w wierszu Swap** — dodaj go, zanim cokolwiek zbudujesz.
`pip install` przy budowaniu obrazu to najbardziej pamieciozerny moment calego
deployu i na 1 GB bez swapu potrafi zostac ubity przez OOM-killer w polowie:

```bash
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

(Na czesci kontenerow LXC `swapon` jest zablokowany. Jesli odmowi — nie walcz z tym,
przejdz do **planu B** w sekcji 5.)

---

## 2. Kod na serwerze

```bash
mkdir -p /opt && cd /opt
git clone <adres-twojego-repo> video-generator
cd video-generator
```

Repo jest prywatne? Najprosciej wgrac je bez gita, z lokalnej maszyny:

```bash
rsync -av --exclude .git --exclude .venv --exclude data --exclude .env \
  ./ root@srvXX.mikr.us:/opt/video-generator/
```

`--exclude data` jest tu istotny: lokalne `data/` ma ~29 MB starych klipow
i nie ma po co jechac na serwer.

---

## 3. Plik `.env`

`.env` jest w `.gitignore`, wiec na serwerze tworzysz go recznie:

```bash
cd /opt/video-generator
cp .env.prod.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # wklej do SESSION_SECRET
nano .env
```

Uzupelnij:

- `BASE_URL` — **dokladnie** adres z paska przegladarki, ze schematem `https://`
  i bez ukosnika na koncu. Stad bierze sie link w mailu logujacym
  (`app/api/auth.py:74`), wiec literowka = niedzialajace logowanie.
- `SESSION_SECRET` — wygenerowany wyzej. Zmiana tej wartosci wylogowuje wszystkich.
- `RESEND_API_KEY` — z panelu Resend.
- `SEMAPHORE_LIMIT=1` — zostaw. Na 1 vCPU 4 rownolegle lancuchy scen to 4 procesy
  ffmpeg naraz.
- `MAX_ACTIVE_JOBS_PER_USER=1` — zostaw. Limit dotyczy **jednego uzytkownika**,
  nie calej instancji: dwoch zalogowanych dalej moze generowac naraz.
- `RETENTION_DAYS=7` / `CLEANUP_INTERVAL_H=24` — domyslne wartosci sa dobre.
  Szczegoly tego, co znika a co zostaje, w §8.

---

## 4. Trzy ustawienia, na ktorych aplikacja odmowi startu

`_check_config()` celowo wywraca proces zamiast wstac w niebezpiecznej konfiguracji.
Jesli kontener wstaje i natychmiast pada, przyczyna jest prawie zawsze tutaj:

| Blad w logach | Znaczenie |
|---|---|
| `SESSION_SECRET must be set when COOKIE_SECURE=true` | puste `SESSION_SECRET` |
| `MAIL_PROVIDER=console is a development backdoor` | zostawiony `console` mimo HTTPS |
| `RESEND_API_KEY must be set when MAIL_PROVIDER=resend` | brak klucza Resend |

To nie sa ostrzezenia do obejscia. Kazde z nich opisuje stan, w ktorym publiczna
instancja jest otwarta na cudze logowanie.

---

## 5. Budowa i uruchomienie

```bash
cd /opt/video-generator
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f app
```

W logach szukasz linii startowej uvicorna oraz — jesli w bazie byly niedokonczone
joby — `[startup] N przerwanych jobow oznaczonych jako 'interrupted'`.

**Plan B, gdy build nie miesci sie w pamieci VPS-a.** Zbuduj obraz lokalnie
i przeslij gotowy, omijajac `pip install` na serwerze:

```bash
# lokalnie
docker build -t video-generator:latest .
docker save video-generator:latest | gzip | ssh root@srvXX.mikr.us -p <port> 'gunzip | docker load'
```

Potem na serwerze zamien w `docker-compose.prod.yml` linie `build: .` na
`image: video-generator:latest` i uruchom bez `--build`.

### Limity pamieci

`mem_limit: 700m` w compose dziala tylko, jesli kernel gospodarza wystawia
odpowiednie cgroups. Pod LXC docker potrafi wypisac ostrzezenie w rodzaju
*"Your kernel does not support memory limit capabilities"* i wpis zignorowac.
Kontener wtedy wstanie — ale bez limitu. Sprawdz:

```bash
docker inspect -f '{{.HostConfig.Memory}}' $(docker compose -f docker-compose.prod.yml ps -q app)
```

`0` znaczy „limit nie obowiazuje". Wtedy jedyna realna ochrona przed OOM to
`SEMAPHORE_LIMIT=1` i swap z sekcji 1.

---

## 6. Weryfikacja

Kolejnosc od najblizszej do najdalszej — pierwszy krok, ktory zawiedzie, wskazuje warstwe.

```bash
# 1. aplikacja w kontenerze
docker compose -f docker-compose.prod.yml exec app \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"

# 2. port na VPS-ie
curl -sI http://127.0.0.1:30108/ | head -1

# 3. port publiczny (z twojego komputera)
curl -sI http://bob108.mikrus.xyz:30108/ | head -1

# 4. HTTPS przez subdomene (z twojego komputera)
curl -sI https://ai-video-generator.cytr.us/ | head -1
```

Jesli 1–3 dzialaja, a 4 nie — problem jest w przypisaniu subdomeny do portu
w panelu Mikrusa, nie w aplikacji.

Na koniec przejdz flow w przegladarce: wejdz na subdomene, podaj maila, kliknij
link z Resenda, wgraj 2–3 zdjecia, zaplanuj sceny, wygeneruj. Przy
`MOCK_PROVIDER=true` powstanie MP4 ze zdjeciem i wypalonym tekstem promptu — to
znaczy, ze caly lancuch (upload → walidacja → plan → ffmpeg → stitch) dziala.

Test wznawiania (rzecz, ktora na VPS psuje sie najczesciej, bo restarty sa realne):
w trakcie generacji `docker compose -f docker-compose.prod.yml restart app`, potem
w „Moje filmy" kliknij **Wznow**.

---

## 7. Aktualizacja wersji

```bash
cd /opt/video-generator
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

`--build` jest **obowiazkowy**: produkcyjny compose nie montuje `./app` do
kontenera, wiec sam `git pull` zmienia pliki na dysku i nic wiecej. To celowe —
kontener zawsze wstaje z kodu wpieczonego w obraz.

Katalog `./data` jest woluminem i przezywa kazdy rebuild oraz `down`. Nie kasuj go.

---

## 8. Dysk, backup, sprzatanie

### Co sprzata sie samo (od 0.5.0)

Aplikacja ma wlasna petle retencji (`app/services/cleanup.py`), uruchamiana
w `lifespan`. Nie wymaga wpisu w cronie — pierwszy przebieg leci przy starcie
kontenera, kolejne co `CLEANUP_INTERVAL_H`.

| Co | Kiedy znika |
|---|---|
| `data/uploads/{job}`, `data/frames/{job}`, `data/clips/{job}` | automatycznie po `RETENTION_DAYS` (7) |
| `data/stitch/{job}` (pliki robocze ffmpega) | kasowane od razu po stitchu; retencja to tylko zapas |
| `data/final/{job}.mp4` | **nigdy sam** — tylko przycisk „Usun" w UI / `DELETE /jobs/{id}` |
| katalog roboczy bez wiersza w bazie (sierota) | automatycznie, po tym samym progu wieku |

Job po wyczyszczeniu **zostaje** w „Moje filmy": film dalej mozna odtworzyc
i pobrac, tylko `Wznow` znika (zamiast niego napis `pliki usuniete`), bo kazda
sciezka za tym przyciskiem potrzebuje zdjec zrodlowych.

### To wciaz wymaga oka

Retencja **nie dotyka gotowych filmow**, wiec `data/final` rosnie bez konca.
Na 10 GB dzielonych z systemem to dalej jest zegar, tylko wolniejszy.

```bash
du -sh /opt/video-generator/data/*
df -h /
```

Wymuszenie sprzatania bez czekania na kolejny przebieg — po prostu restart:

```bash
docker compose -f docker-compose.prod.yml restart app
docker compose -f docker-compose.prod.yml logs --tail=50 app | grep cleanup
```

Awaryjnie, gdy dysk jest juz pelny i aplikacja nie wstaje, reczny odpowiednik
(**wiersze w bazie zostaja bez znacznika `workdirs_purged_at`**, wiec UI dalej
zaoferuje `Wznow`, ktory padnie — to jest cena obejscia):

```bash
find /opt/video-generator/data/frames  -type f -mtime +7 -delete
find /opt/video-generator/data/clips   -type f -mtime +7 -delete
find /opt/video-generator/data/uploads -type f -mtime +7 -delete
find /opt/video-generator/data/stitch  -type f -mtime +1 -delete
# data/final kasuj swiadomie - to gotowe filmy uzytkownikow
```

Osierocone pliki `*.part` (znane ograniczenie timeoutu w `FalProvider` — anulowane
pobieranie nie zabija watku w puli) mozna czyscic bez ceregieli:

```bash
find /opt/video-generator/data -name '*.part' -mtime +1 -delete
```

### Backup bazy

`data/video_gen.db` to jedyne zrodlo prawdy o jobach — bez niego pliki na dysku
sa anonimowymi MP4. `scripts/backup-db.sh` robi kopie przez `sqlite3.backup()`
(a nie `cp`, ktore na otwartej bazie moze zlapac plik w polowie transakcji)
i rotuje starsze niz 7 dni.

```bash
crontab -e
# 0 3 * * * cd /opt/video-generator && ./scripts/backup-db.sh >> /var/log/vg-backup.log 2>&1
```

Backupy ladaja w `data/backups/` — czyli **na tym samym dysku**. To chroni przed
uszkodzeniem bazy, nie przed utrata VPS-a. Kopia poza serwer:

```bash
rsync -av root@srvXX.mikr.us:/opt/video-generator/data/backups/ ~/backups/video-gen/
```

---

## 9. Przelaczenie na realnego providera

Dopiero po przejsciu sekcji 6 w calosci:

```bash
nano .env     # MOCK_PROVIDER=false, uzupelnij FAL_KEY i OPENAI_API_KEY
docker compose -f docker-compose.prod.yml up -d
```

Rebuild nie jest potrzebny — `MOCK_PROVIDER` to zmienna srodowiskowa, ale
`Settings` czyta ja **raz, przy imporcie**, wiec kontener musi wstac na nowo.
Uwaga: `docker compose restart` tu **nie wystarczy** — zmienne srodowiskowe sa
przypisywane przy tworzeniu kontenera, wiec potrzebny jest `up -d`, ktory go podmieni.

Pierwszy realny job zrob na 2 scenach po 5 s najtanszym modelem (`wan`, $0.05/s
wg `model_costs`) — to okolo $0.50. Ekran planu pokazuje `est_cost_usd` przed
kliknieciem **Generuj**.

Nie ma systemu kredytow ani limitu jobow na uzytkownika (roadmap Faza 3 i 1.3).
Kazdy, kto sie zaloguje, wydaje twoje pieniadze — dopoki tego nie ma, trzymaj
adres dla siebie.

---

## 10. Gdy cos nie dziala

```bash
# logi (produkcyjny compose trzyma 3 × 10 MB)
docker compose -f docker-compose.prod.yml logs --tail 200 app

# czy kontener zyje i co mowi healthcheck
docker compose -f docker-compose.prod.yml ps

# restart bez rebuildu
docker compose -f docker-compose.prod.yml restart app
```

| Objaw | Najczestsza przyczyna |
|---|---|
| Kontener restartuje sie w kolko | `_check_config()` — patrz sekcja 4, blad jest w pierwszych liniach logu |
| Logowanie: link z maila prowadzi donikad | `BASE_URL` != adres w przegladarce |
| Zalogowany, ale przy odswiezeniu wylatuje | `COOKIE_SECURE=true` przy wejsciu po http, albo zmienione `SESSION_SECRET` |
| Pasek postepu stoi, choc job idzie | SSE (`/jobs/{id}/events`) nie przechodzi przez proxy — sprawdz w konsoli przegladarki |
| Generacja pada w polowie | OOM. `dmesg \| tail` na VPS; zejdz z `SEMAPHORE_LIMIT`, dodaj swap |
| „Brak miejsca na urzadzeniu" | sekcja 8 |

Job, ktory padl przez restart, ma status `interrupted` i przycisk **Wznow** —
sceny juz wygenerowane nie beda generowane (ani placone) drugi raz.

---

## 11. Wysylka maili do dowolnych adresow (wlasna domena w Resend)

Domyslny nadawca `onboarding@resend.dev` dostarcza **wylacznie na adres wlasciciela
konta Resend**. To ograniczenie nadawcy, nie odbiorcy — aplikacja wysyla poprawnie,
Resend po prostu odmawia doreczenia. Zeby zalogowac mogl sie ktokolwiek, trzeba
nadawac z domeny, ktora sie kontroluje.

**Subdomena od Mikrusa (`*.cytr.us`) sie nie nada** — weryfikacja polega na dopisaniu
rekordow do DNS domeny, a `cytr.us` nalezy do Mikrusa.

### Kroki

Domena uzywana w tym wdrozeniu: **`ai-video-generator.pl`** (rejestrator: nazwa.pl).
Sluzy *wylacznie* do wysylki maili — aplikacja zostaje pod `ai-video-generator.cytr.us`,
`BASE_URL` sie **nie zmienia**.

1. ~~**Kup domene**~~ — ZROBIONE (`ai-video-generator.pl`).
2. **Resend → Domains → Add Domain: `mail.ai-video-generator.pl`.** Celowo
   **subdomena**, nie domena glowna: gdyby reputacja nadawcy kiedys ucierpiala,
   nie pociagnie za soba poczty na `@ai-video-generator.pl`, gdybys ja kiedys zalozyl.
   Przy dodawaniu wybierasz **region** (np. `eu-west-1`) — wchodzi on do nazw
   rekordow, wiec pozniejsza zmiana regionu = ponowna weryfikacja.
3. **Wklej wygenerowane rekordy do DNS.** Ponizej ksztalt tego, co Resend daje
   dla domen zakladanych **po sierpniu 2026**; **wartosci zawsze kopiuj z panelu
   Resend**, nie stad:

   | Typ | Host (pelny) | Wartosc |
   |---|---|---|
   | `TXT` | `resend._domainkey.mail.ai-video-generator.pl` | `p=MIGfMA0GCSq...` (DKIM, ~400 znakow) |
   | `CNAME` | `send.mail.ai-video-generator.pl` | `send.forge.rmta.net` |
   | `CNAME` | `rsend.mail.ai-video-generator.pl` | `rsend-euw1.forge.rmta.net` |
   | `TXT` | `_dmarc.mail.ai-video-generator.pl` | `v=DMARC1; p=none;` (opcjonalny, poprawia dostarczalnosc) |

   Starsze konta dostawaly zamiast dwoch CNAME-ow rekord `MX` + `TXT` ze SPF na
   `amazonses.com` — jesli panel Resend pokazuje taki komplet, kieruj sie nim.
   **Kazdy CNAME weryfikuje sie osobno**, wiec przy jednym poprawnym domena potrafi
   utknac w stanie `partially_verified`.

   **Gdzie to jest w nazwa.pl.** Zakladka nie nazywa sie "Rekordy DNS":
   `nazwa.pl/panel` → **Uslugi → Domeny** → przy domenie, po **prawej**, maly
   odnosnik **`konfiguruj`** → zakladka **"Reczna konfiguracja DNS"**. Lista
   rekordow jest widoczna od razu, ale **tylko do odczytu** — edycje odblokowuje
   przycisk **`ZMIEN`** na samym **dole** listy. To miejsce, w ktorym najlatwiej
   utknac: wyglada, jakby nic nie dalo sie kliknac.

   Gdy w ogole nie ma `konfiguruj` / recznej konfiguracji, sprawdz dwie rzeczy:
   czy domena nie jest delegowana na obce serwery NS (wtedy strefa jest gdzie
   indziej i edycja w nazwa.pl nic nie da) oraz czy reczna konfiguracja strefy
   jest w ogole wlaczona — nazwa.pl trzyma to za osobnym przelacznikiem.

   **Pulapka 1 — "Niedozwolony wpis w nazwie".** Panel nazwa.pl wymaga w polu
   nazwy **pelnej nazwy (FQDN)**, a nie czesci wzglednej: `send.mail.ai-video-generator.pl`,
   nie `send.mail`. Skrocona nazwa konczy sie bledem *Niedozwolony wpis w nazwie* —
   komunikat znaczy dokladnie tyle, ze wpisana nazwa nie nalezy do tej strefy.
   To odwrotnie niz w wiekszosci paneli (Cloudflare, OVH), ktore sufiks domeny
   dokladaja same — stad latwo tu wpasc.

   Objaw bywa mylacy, bo walidacja **TXT jest lagodniejsza niz CNAME**: ten sam
   komplet od Resenda potrafi przejsc w czesci (DKIM `wykonany`) i wywalic sie
   na obu CNAME-ach, mimo ze wszystkie trzy wpisy sa przepisane poprawnie.
   Zanim zaczniesz szukac winy w rekordach, sprawdz na liscie, jak rekord
   *naprawde* sie nazywa.

   **Czego NIE trzeba ruszac.** Swieza strefa zawiera `*.ai-video-generator.pl` (A)
   oraz `mail.ai-video-generator.pl` (A) — wpisy nazwa.pl pod wlasny hosting.
   Zaden z nich nie blokuje CNAME-ow Resenda: `mail...` i `send.mail...` to dwie
   rozne nazwy, wiec zakaz wspolistnienia CNAME z innymi rekordami ich nie dotyczy
   (a `mail...` i tak nie daje sie usunac). Na pustej domenie wildcard mozna
   skasowac dla higieny, ale to nie jest lekarstwo na nic.

   **Cudzyslowy przy TXT sa zbedne** — panel doklada je sam przy zapisie do strefy.
   Klucz DKIM (~400 znakow) wklej jednym ciagiem, bez spacji i bez lamania linii;
   nazwa.pl przyjmuje go bez protestu.

   **Gdy panel jednak odrzuci DKIM**: przenies **samo DNS** do Cloudflare — domena
   zostaje w nazwa.pl, zmieniasz tylko serwery nazw. Rekordy dodaje sie wtedy bez
   walki, a propagacja jest w sekundach zamiast godzin.
4. **Poczekaj i kliknij Verify.** Propagacja to zwykle minuty, czasem godziny.
   Sprawdzenie z wlasnego terminala, zanim zaczniesz klikac Verify w kolko:
   ```bash
   dig +short TXT   resend._domainkey.mail.ai-video-generator.pl
   dig +short CNAME send.mail.ai-video-generator.pl
   dig +short CNAME rsend.mail.ai-video-generator.pl
   ```
   Odpowiedz `send.forge.rmta.net.ai-video-generator.pl.` znaczy, ze panel dokleil
   nazwe domeny takze do **wartosci** CNAME — zakoncz ja wtedy kropka
   (`send.forge.rmta.net.`), ktora oznacza nazwe absolutna.
   Pusta odpowiedz = rekord jeszcze nie propagowal **albo** ma zla nazwe (patrz
   pulapka wyzej). Dopoki Resend nie pokaze `Verified`, nie ruszaj dalej.
5. **Na VPS, w `.env`:**
   ```bash
   MAIL_FROM=Video Generator <no-reply@mail.ai-video-generator.pl>
   ```
   Adres **musi** byc w zweryfikowanej domenie. Inny → Resend odrzuca zadanie.
   `no-reply@` jest tu swiadome: skrzynka nie istnieje i nikt nie czyta odpowiedzi,
   a rekord MX z punktu 3 obsluguje wylacznie odbicia dla Resenda.
6. **Odtworz kontener** — `up -d`, a **nie** `restart`:
   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```
   `docker compose restart` restartuje proces w **tym samym** kontenerze, a zmienne
   srodowiskowe sa ustalane w chwili jego tworzenia — zobaczylby stary `MAIL_FROM`.
   `up -d` wykrywa zmiane w `.env` i podmienia kontener na nowy.
7. **Test z innego adresu niz wlasny.** To jedyny test, ktory cokolwiek dowodzi —
   na swoj wlasny mail dochodzilo takze przed zmiana.

### Co sprawdzic, gdy mail nie dochodzi

Aplikacja zwraca wtedy `502 Nie udalo sie wyslac emaila`, a **prawdziwy powod jest
w logach kontenera** (`api/auth.py` loguje `Mail send failed for ...`):

```bash
docker compose -f docker-compose.prod.yml logs --tail 50 app | grep -i "mail send failed"
```

| W logu | Przyczyna |
|---|---|
| `Resend error 403` | `MAIL_FROM` spoza zweryfikowanej domeny albo domena jeszcze nie `Verified` |
| `Resend error 401` | zly `RESEND_API_KEY` |
| `Resend error 422` | zly format `MAIL_FROM` (musi byc `Nazwa <adres@domena>` albo samo `adres@domena`) |
| brak wpisu, uzytkownik nie widzi maila | mail poszedl — szukaj w spamie; jesli tam jest, dodaj rekord DMARC |

Darmowy plan Resend to **100 maili na dobe** i 3000 miesiecznie. Kazde kliniecie
"zaloguj" to jeden mail, a limit `login_rate_per_email` (3 na 15 min) tego nie
zastapi — pilnuje jednego adresu, nie calej puli.

### Zanim otworzysz logowanie szerzej

Nie ma systemu kredytow (Faza 3) ani limitu jobow na uzytkownika (Faza 1.3).
Przy `MOCK_PROVIDER=false` **kazdy zalogowany generuje filmy za twoje pieniadze**,
bez gornej granicy. Dopoki tego nie ma, otwarte logowanie ma sens tylko z
`MOCK_PROVIDER=true` — wtedy kosztuje wylacznie miejsce na dysku, ktorego rowniez
nikt nie sprzata (sekcja 8).
