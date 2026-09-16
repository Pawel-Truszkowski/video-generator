"""Credit ledger: pricing, reservation, settlement.

The only boundary between the app and the `credits` table / `users.credit_balance`.

Two rules the whole module rests on:

1. **`users.credit_balance` is authoritative; SUM(credits.amount) is an audit
   trail.** `app/db.py` holds one shared aiosqlite connection with no explicit
   transactions, so only a single SQL statement is atomic here — "SELECT SUM,
   check, INSERT" is the same TOCTOU window `consume_magic_token` avoids.
2. **A credit is one US cent, always an int.** Rounding happens once, in
   `credit_rate()`; everything downstream is integer, so the ledger sums exactly.

A job's accounting derives entirely from ledger rows carrying its `job_id` —
there is no separate reservation state:

    spent_net = -SUM(amount WHERE job_id = ?)      charged and not yet returned
    actual    = credits_for(model, scenes 'done')  what was actually rendered
    refund    = spent_net - actual                 posted as a 'refund' row

That formula is idempotent (a second call yields 0) and handles retry and resume
on its own, since each adds its own `usage` row.
"""

from __future__ import annotations

import math

from app.config import settings
from app.db import get_db
from app.services.job_state import cost_per_second

# Same time convention as auth/store.py: datetime('now') has no zone marker and
# the browser reads it as local time.
NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"

# Entry types. purchase/bonus add, usage subtracts, refund returns part of usage.
PURCHASE, USAGE, REFUND, BONUS = "purchase", "usage", "refund", "bonus"


class InsufficientCredits(Exception):
    """Raised by charge(); the API layer turns this into a 402."""

    def __init__(self, needed: int, available: int):
        self.needed = needed
        self.available = available
        super().__init__(f"needs {needed} credits, {available} available")


# --- Pricing ------------------------------------------------------------------

def credit_rate(model: str) -> int:
    """Credits per second of video for `model`.

    Derived from model_costs (USD/s) times the margin rather than kept in a
    second price table, which would drift from fal.ai's the first time their
    pricing changes. Raises UnknownModelError for a model with no price.
    """
    exact = cost_per_second(model) * 100 * settings.credit_margin
    # round() before ceil() is load-bearing: 0.07 * 100 * 2.0 is
    # 14.000000000000002, so bare ceil() prices Kling at 15 credits/s instead of
    # 14. A genuine half credit still rounds up.
    return math.ceil(round(exact, 6))


def credits_for(model: str, scenes: list[dict]) -> int:
    """Cost of a scene list in credits. An empty list costs 0."""
    rate = credit_rate(model)
    return sum(rate * int(s["duration_s"]) for s in scenes)


# --- Read ---------------------------------------------------------------------

async def balance(user_id: str) -> int:
    db = await get_db()
    cur = await db.execute("SELECT credit_balance FROM users WHERE id = ?", (user_id,))
    row = await cur.fetchone()
    return int(row["credit_balance"]) if row else 0


async def job_net_spent(job_id: str) -> int:
    """What this job has cost its owner net of refunds.

    A positive number means "taken from the account and not yet given back".
    """
    db = await get_db()
    cur = await db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM credits WHERE job_id = ?", (job_id,)
    )
    row = await cur.fetchone()
    return -int(row["total"])


async def history(user_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT id, amount, type, job_id, description, balance_after, created_at "
        "  FROM credits WHERE user_id = ? "
        " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    )
    return [dict(r) for r in await cur.fetchall()]


# --- Write --------------------------------------------------------------------

async def grant(
    user_id: str,
    amount: int,
    type: str,
    description: str,
    ext_id: str | None = None,
    job_id: str | None = None,
) -> int | None:
    """Add credits. Returns the new balance, or None if `ext_id` was already used.

    Ledger row first, balance second: INSERT OR IGNORE on UNIQUE(ext_id) is what
    makes this idempotent, and in the other order a retried Stripe webhook would
    have topped the account up before the duplicate was noticed.
    """
    if amount == 0:
        raise ValueError("A zero-credit entry is meaningless")

    db = await get_db()

    cur = await db.execute(
        f"INSERT OR IGNORE INTO credits "
        f"  (user_id, amount, type, job_id, ext_id, description, balance_after, created_at) "
        f"VALUES (?, ?, ?, ?, ?, ?, 0, {NOW})",
        (user_id, amount, type, job_id, ext_id, description),
    )
    if cur.rowcount == 0:
        # Already applied. A silent no-op rather than an error: the webhook must
        # answer 200 or Stripe keeps retrying for days.
        await db.commit()
        return None

    entry_id = cur.lastrowid

    cur = await db.execute(
        "UPDATE users SET credit_balance = credit_balance + ? WHERE id = ? "
        "RETURNING credit_balance",
        (amount, user_id),
    )
    row = await cur.fetchone()
    new_balance = int(row["credit_balance"])

    await db.execute(
        "UPDATE credits SET balance_after = ? WHERE id = ?", (new_balance, entry_id)
    )
    await db.commit()
    return new_balance


async def try_charge(user_id: str, amount: int, job_id: str, description: str) -> int | None:
    """Take `amount` credits, but only if the account covers it.

    Returns the new balance, or None when funds were short (changing nothing).
    """
    if amount <= 0:
        raise ValueError("A charge must be positive")

    db = await get_db()

    # The funds check belongs to the modifying statement, not a separate SELECT:
    # the `await` between check and debit yields to the event loop, so two
    # concurrent requests would let a charge through past the balance. Same idiom
    # as UPDATE ... WHERE used_at IS NULL in consume_magic_token. No row in
    # RETURNING means the WHERE did not match, i.e. there were no funds.
    cur = await db.execute(
        "UPDATE users SET credit_balance = credit_balance - ? "
        "WHERE id = ? AND credit_balance >= ? "
        "RETURNING credit_balance",
        (amount, user_id, amount),
    )
    row = await cur.fetchone()

    if row is None:
        return None

    new_balance = int(row["credit_balance"])
    await db.execute(
        f"INSERT INTO credits "
        f"  (user_id, amount, type, job_id, ext_id, description, balance_after, created_at) "
        f"VALUES (?, ?, ?, ?, NULL, ?, ?, {NOW})",
        (user_id, -amount, USAGE, job_id, description, new_balance),
    )
    await db.commit()
    return new_balance


async def charge(user_id: str, amount: int, job_id: str, description: str) -> int:
    """try_charge(), but short funds raise instead of returning None.

    Convenient in the API layer, where it ends as a 402 anyway.
    """
    if amount <= 0:
        return await balance(user_id)

    new_balance = await try_charge(user_id, amount, job_id, description)
    if new_balance is None:
        raise InsufficientCredits(amount, await balance(user_id))
    return new_balance


# --- Settlement ---------------------------------------------------------------

async def settle_job(job_id: str) -> int:
    """Return the excess of the reservation over what was rendered.

    Scenes are read from the DB, not from `state`: state can be stale after an
    exception, and settlement must describe what is on disk.

    Idempotent — after a refund spent_net equals actual, so a second call
    computes 0. That is what lets both the pipeline's `finally` and the startup
    sweep call it without coordinating.
    """
    db = await get_db()
    cur = await db.execute("SELECT user_id, model FROM jobs WHERE id = ?", (job_id,))
    job = await cur.fetchone()
    if job is None or job["user_id"] is None:
        # Pre-0.2.0 jobs have no owner, so there is nobody to refund.
        return 0

    spent_net = await job_net_spent(job_id)
    if spent_net <= 0:
        return 0

    cur = await db.execute(
        "SELECT duration_s FROM scenes WHERE job_id = ? AND status = 'done'", (job_id,)
    )
    done_scenes = [dict(r) for r in await cur.fetchall()]
    actual = credits_for(job["model"], done_scenes)

    refund = spent_net - actual
    if refund <= 0:
        return 0

    await grant(
        job["user_id"],
        refund,
        REFUND,
        f"Zwrot za niewykorzystane sceny (job {job_id})",
        job_id=job_id,
    )
    return refund


# --- Welcome credits ----------------------------------------------------------

async def sync_welcome_credits(user_id: str) -> None:
    """Grant the welcome credits, once per account.

    Idempotent on ext_id = "welcome:<user_id>", so it is safe to call on every
    sign-in and every startup — the same property as sync_admins(): the outcome
    depends on env, not on the DB's history. Deliberate consequence: raising
    WELCOME_CREDITS does not top up existing accounts, since the row exists.
    """
    if settings.welcome_credits <= 0:
        return
    await grant(
        user_id,
        settings.welcome_credits,
        BONUS,
        "Kredyty powitalne",
        ext_id=f"welcome:{user_id}",
    )


async def backfill_welcome_credits() -> int:
    """Grant welcome credits to accounts created before 0.7.0. Returns how many.

    Called from lifespan. The NOT EXISTS clause is an optimisation, not the
    safeguard — UNIQUE(ext_id) in grant() is what makes this run once. Without it
    this would be one query per account on every startup.
    """
    if settings.welcome_credits <= 0:
        return 0

    db = await get_db()
    cur = await db.execute(
        "SELECT id FROM users u WHERE NOT EXISTS ("
        "  SELECT 1 FROM credits c WHERE c.ext_id = 'welcome:' || u.id)"
    )
    user_ids = [r["id"] for r in await cur.fetchall()]
    for user_id in user_ids:
        await sync_welcome_credits(user_id)
    return len(user_ids)
