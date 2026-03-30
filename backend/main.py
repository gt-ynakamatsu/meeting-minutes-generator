"""FastAPI アプリの組み立て（ルーターは `backend/routes/`）。"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import database as db
from backend.auth_settings import auth_enabled
from backend.routes import admin, auth, feedback, jobs, meta, presets, profile, records

_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8085",
).split(",")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    n = db.purge_all_minutes_archives()
    if n:
        logging.getLogger("uvicorn.error").info(
            "議事録の保持期限により %s 件のレコードを削除しました（MM_MINUTES_RETENTION_DAYS）。",
            n,
        )
    if not auth_enabled():
        logging.getLogger("uvicorn.error").warning(
            "MM_AUTH_SECRET が未設定のため認証が無効です。全利用者が同一の議事録 DB（data/minutes.db）を共有します。"
            "ユーザー別アーカイブには MM_AUTH_SECRET を設定してください（Docker Compose 既定ではフォールバック値で認証 ON）。"
        )
    yield


app = FastAPI(title="Meeting Minutes API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (meta, auth, admin, feedback, profile, presets, jobs, records):
    app.include_router(r.router)
