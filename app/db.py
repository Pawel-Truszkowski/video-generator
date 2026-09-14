from __future__ import annotations

import aiosqlite
from app.config import settings

_db: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'uploaded',
    prompt TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT 'wan',
    aspect_ratio TEXT NOT NULL DEFAULT '16:9',
    target_duration_s INTEGER NOT NULL DEFAULT 120,
    est_cost_usd REAL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    idx INTEGER NOT NULL,
    image_path TEXT,
    sub_prompt TEXT,
    duration_s INTEGER NOT NULL DEFAULT 5,
    chain_from_prev INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    clip_path TEXT,
    fal_request_id TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS magic_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_magic_tokens_user ON magic_tokens(user_id);

-- Every read of a job's plan is "all scenes of one job, in order" (load_state,
-- GET /jobs/{id}). Not UNIQUE: an existing DB could already hold duplicate
-- (job_id, idx) pairs and creating the index would then fail at startup.
CREATE INDEX IF NOT EXISTS idx_scenes_job_idx ON scenes(job_id, idx);
"""

# Indexes that reference columns added by _migrate(). These MUST run after the
# ALTERs — inside SCHEMA they would fail with "no such column: user_id" on an
# existing DB and abort the whole executescript, taking the CREATE TABLEs with it.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at DESC);

-- Sweep retencji pyta "joby starsze niz X, jeszcze nie sprzatane". Bez indeksu
-- to full scan po calej tabeli co dobe -- tanio dzis, ale rosnie w nieskonczonosc.
CREATE INDEX IF NOT EXISTS idx_jobs_purge ON jobs(workdirs_purged_at, created_at);
"""


async def _column_names(db: aiosqlite.Connection, table: str) -> set[str]:
    cur = await db.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in await cur.fetchall()}


async def _migrate(db: aiosqlite.Connection) -> None:
    """Idempotent additive migrations. Safe to run on every startup.

    CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so new columns on
    `jobs` must be added here rather than in SCHEMA. Note SQLite's ALTER TABLE
    ADD COLUMN cannot take an expression default, hence nullable + backfill.
    """
    cols = await _column_names(db, "jobs")

    if "user_id" not in cols:
        # No default: pre-existing jobs stay NULL, so `WHERE user_id = ?` never
        # matches them and they are invisible to every user.
        await db.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT REFERENCES users(id)")

    if "created_at" not in cols:
        await db.execute("ALTER TABLE jobs ADD COLUMN created_at TEXT")
        await db.execute(
            "UPDATE jobs SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
            "WHERE created_at IS NULL"
        )

    if "workdirs_purged_at" not in cols:
        # Kiedy retencja skasowala material roboczy joba (uploads/frames/clips).
        # NULL = jeszcze nie sprzatany, co dla starych wierszy jest prawda.
        # Nie da sie tego wywnioskowac z dysku: brak katalogu znaczy tez "job
        # nigdy nie mial plikow" albo "wolumin sie nie zamontowal", a te trzy
        # przypadki wymagaja innej odpowiedzi na probe wznowienia.
        await db.execute("ALTER TABLE jobs ADD COLUMN workdirs_purged_at TEXT")

    scene_cols = await _column_names(db, "scenes")

    if "error" not in scene_cols:
        # Why a column and not just the log: the UI offers a per-scene retry, so
        # the reason a scene failed has to survive a restart and be readable per row.
        await db.execute("ALTER TABLE scenes ADD COLUMN error TEXT")

    if "image_index" not in scene_cols:
        await db.execute(
            "ALTER TABLE scenes ADD COLUMN image_index INTEGER NOT NULL DEFAULT 0"
        )

        # Backfill from the legacy 'image_<N>' label. The startswith() is what
        # makes this exact: in SQL '_' is a single-character wildcard, so
        # LIKE 'image_%' also matches e.g. 'images/foo.jpg'.
        legacy = await db.execute(
            "SELECT id, image_path FROM scenes WHERE image_path LIKE 'image_%'"
        )
        for row in await legacy.fetchall():
            image_path = row["image_path"]
            if image_path is not None and image_path.startswith("image_"):
                try:
                    idx = int(image_path.split("_")[1])
                    await db.execute(
                        "UPDATE scenes SET image_index = ? WHERE id = ?", (idx, row["id"])
                    )
                except ValueError:
                    # Should never happen, but don't crash the whole migration if it does.
                    pass

    user_cols = await _column_names(db, "users")

    if "is_admin" not in user_cols:
        # Stala wartosc domyslna, wiec bez backfillu: istniejace konta sa
        # zwyklymi uzytkownikami, dopoki sync_admins() przy starcie nie
        # przepisze na nie ADMIN_EMAILS.
        await db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")

    await db.commit()


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(settings.db_path)
        _db.row_factory = aiosqlite.Row
        await _db.executescript(SCHEMA)   # tables only
        await _db.commit()
        await _migrate(_db)               # additive ALTERs + backfill
        await _db.executescript(INDEXES)  # indexes depending on the new columns
        await _db.commit()
    return _db


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None
