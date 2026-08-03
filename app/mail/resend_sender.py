from __future__ import annotations

import httpx

from app.config import settings

RESEND_ENDPOINT = "https://api.resend.com/emails"


class ResendMailSender:
    """Sends via the Resend HTTP API.

    Setup note: the free `onboarding@resend.dev` sender only delivers to the
    Resend account owner's own address. A custom MAIL_FROM needs a verified domain.
    """

    async def send_login_link(self, to_email: str, login_url: str) -> None:
        ttl = settings.magic_link_ttl_min
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                RESEND_ENDPOINT,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={
                    "from": settings.mail_from,
                    "to": [to_email],
                    "subject": "Twoj link do logowania - Video Generator",
                    "text": (
                        f"Kliknij aby sie zalogowac (link wazny {ttl} min):\n\n{login_url}\n\n"
                        "Jesli to nie Ty prosiles o logowanie, zignoruj ta wiadomosc."
                    ),
                    "html": (
                        f"<p>Kliknij aby sie zalogowac (link wazny {ttl} min):</p>"
                        f'<p><a href="{login_url}">Zaloguj sie</a></p>'
                        "<p style=\"color:#888;font-size:12px\">"
                        "Jesli to nie Ty prosiles o logowanie, zignoruj ta wiadomosc.</p>"
                    ),
                },
            )
        if res.status_code >= 300:
            raise RuntimeError(f"Resend error {res.status_code}: {res.text}")
