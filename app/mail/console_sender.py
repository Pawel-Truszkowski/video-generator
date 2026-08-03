from __future__ import annotations


class ConsoleMailSender:
    """Prints the login link instead of sending it. Development only — see the
    warning in .env.example: this makes the link readable by anyone with log access."""

    async def send_login_link(self, to_email: str, login_url: str) -> None:
        # flush=True: without it the link can sit in a buffer and never reach
        # `docker compose logs`.
        print(
            f"\n=== MAGIC LINK for {to_email} ===\n{login_url}\n"
            f"=== (MAIL_PROVIDER=console) ===\n",
            flush=True,
        )
