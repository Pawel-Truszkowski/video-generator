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
"""


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(settings.db_path)
        _db.row_factory = aiosqlite.Row
        await _db.executescript(SCHEMA)
        await _db.commit()
    return _db


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None
