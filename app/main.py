from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import get_db, close_db
from app.api.jobs import router as jobs_router
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.data_dir, exist_ok=True)
    await get_db()
    yield
    await close_db()


app = FastAPI(title="Video Generator POC", lifespan=lifespan)

app.include_router(jobs_router)

# Serve static files (frontend)
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
