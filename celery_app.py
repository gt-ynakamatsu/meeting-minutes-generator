"""Celery アプリ（API からはこのモジュールのみ import し、tasks の重い依存を引かない）。"""
import os

from celery import Celery

celery_app = Celery(
    "tasks",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
)
