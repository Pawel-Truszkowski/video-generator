from __future__ import annotations

import uuid

import aiosqlite

from app.auth.tokens import hash_token
from app.config import settings
from app.db import get_db

# ISO-8601 with an explicit Z. NOT datetime('now'), which yields
# "2026-07-30 12:34:56" — no zone marker, so new Date() in the browser parses it
# as local time and every date in the UI silently shifts by the UTC offset.
NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_or_create_user(email: str) -> aiosqlite.Row:
    """Sign-up is sign-in: no caller can tell a new email from an existing one,
    so there is no account-enumeration surface."""
    email = normalize_email(email)
    db = await get_db()

    cur = await db.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = await cur.fetchone()
    if user is not None:
        return user

    user_id = uuid.uuid4().hex[:12]
    await db.execute(
        f"INSERT INTO users (id, email, created_at) VALUES (?, ?, {NOW})",
        (user_id, email),
    )
    await db.commit()

    cur = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return await cur.fetchone()


async def create_magic_token(user_id: str, raw_token: str) -> None:
    """Store the hash of a fresh token, invalidating any outstanding ones.

    Capping live tokens at one per user means requesting a second link kills the
    first — standard behaviour, but worth knowing when testing.
    """
    db = await get_db()

    await db.execute(
        f"UPDATE magic_tokens SET used_at = {NOW} WHERE user_id = ? AND used_at IS NULL",
        (user_id,),
    )
    await db.execute(
        f"INSERT INTO magic_tokens (token_hash, user_id, created_at, expires_at) "
        f"VALUES (?, ?, {NOW}, strftime('%Y-%m-%dT%H:%M:%SZ','now','+{int(settings.magic_link_ttl_min)} minutes'))",
        (hash_token(raw_token), user_id),
    )
    # Opportunistic GC of long-dead tokens.
    await db.execute(
        "DELETE FROM magic_tokens WHERE expires_at < strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 day')"
    )
    await db.commit()


async def consume_magic_token(raw_token: str) -> str | None:
    """Atomically spend a token. Returns the user_id, or None if it was invalid,
    already used, or expired.

    Single UPDATE ... WHERE used_at IS NULL + rowcount, never SELECT-then-UPDATE:
    the latter is a TOCTOU window even on one connection, because the awaits
    between the two statements let another request interleave. Expiry is compared
    in SQL so there is no Python/SQLite clock or format mismatch.
    """
    if not raw_token:
        return None

    db = await get_db()
    token_hash = hash_token(raw_token)

    cur = await db.execute(
        f"UPDATE magic_tokens SET used_at = {NOW} "
        f"WHERE token_hash = ? AND used_at IS NULL AND expires_at > {NOW}",
        (token_hash,),
    )
    await db.commit()

    if cur.rowcount != 1:
        return None

    cur = await db.execute("SELECT user_id FROM magic_tokens WHERE token_hash = ?", (token_hash,))
    row = await cur.fetchone()
    if row is None:
        return None

    # A deactivated account must not be revivable by an old link.
    cur = await db.execute(
        "SELECT id FROM users WHERE id = ? AND is_active = 1", (row["user_id"],)
    )
    user = await cur.fetchone()
    return user["id"] if user else None


async def touch_last_login(user_id: str) -> None:
    db = await get_db()
    await db.execute(f"UPDATE users SET last_login_at = {NOW} WHERE id = ?", (user_id,))
    await db.commit()
