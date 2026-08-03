from __future__ import annotations

from app.config import settings
from app.mail.base import MailSender
from app.mail.console_sender import ConsoleMailSender
from app.mail.resend_sender import ResendMailSender

__all__ = ["MailSender", "get_sender"]


def get_sender() -> MailSender:
    """Pick the mail backend, mirroring _get_provider() in graph/nodes/generate_clips.py.

    Unknown values fall back to console; the lifespan guard in main.py catches
    MAIL_PROVIDER=resend without an API key.
    """
    if settings.mail_provider == "resend":
        return ResendMailSender()
    return ConsoleMailSender()
