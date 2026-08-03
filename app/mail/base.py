from __future__ import annotations

from typing import Protocol


class MailSender(Protocol):
    async def send_login_link(self, to_email: str, login_url: str) -> None:
        """Deliver a magic-link login email. Raises on hard delivery failure."""
        ...
