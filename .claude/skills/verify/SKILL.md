---
name: verify
description: Uruchom aplikację w izolowanym kontenerze (mock, osobny port i dane) i przejdź flow przez API/UI.
---

# Weryfikacja w izolowanym kontenerze

`.env` może mieć `MOCK_PROVIDER=false`, a port 8000 bywa zajęty przez inny projekt,
a `data/` to prawdziwa baza. Dlatego nie używaj `docker compose up` do weryfikacji:

```bash
D=$(mktemp -d)
docker build -q -t vg-verify .
docker run -d --name vg-verify -p 8011:8000 --env-file .env \
  -e MOCK_PROVIDER=true -e STRIPE_ENABLED=false \
  -v $D:/data -v "$PWD/app":/app/app vg-verify
# start trwa ~45 s (bind-mount na macOS) — odpytuj /health w pętli
```

Logowanie: `POST /auth/request-login` z `{"email": ...}` zwraca `login_url`
(z portem 8000 — podmień na 8011); `curl -c cookies.txt` na ten URL daje sesję.

Flow: `POST /jobs` (multipart: `images`, `model`, `plan_mode`, `prompt`, `scenes` JSON)
→ `POST /jobs/{id}/generate` → poll `GET /jobs/{id}` aż `status=done`.

Sprzątanie: `docker rm -f vg-verify && docker rmi vg-verify`; usuń `.playwright-mcp/`,
jeśli używałeś Playwrighta (zapisuje tylko w obrębie repo).
