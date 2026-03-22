import json
import sqlite3
from datetime import datetime, timedelta
import os

DB_PATH = os.path.join("data", "minutes.db")


def _migrate_records_columns(conn):
    cur = conn.execute("PRAGMA table_info(records)")
    existing = {row[1] for row in cur.fetchall()}
    additions = [
        ("topic", "TEXT DEFAULT ''"),
        ("tags", "TEXT DEFAULT ''"),
        ("category", "TEXT DEFAULT ''"),
        ("meeting_date", "TEXT DEFAULT ''"),
        ("preset_id", "TEXT DEFAULT ''"),
        ("context_json", "TEXT DEFAULT ''"),
    ]
    for col, decl in additions:
        if col not in existing:
            conn.execute(f"ALTER TABLE records ADD COLUMN {col} {decl}")


def init_db():
    os.makedirs("data", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY,
                email TEXT,
                filename TEXT,
                status TEXT,
                transcript TEXT,
                summary TEXT,
                created_at TIMESTAMP
            )
            """
        )
        _migrate_records_columns(conn)


def save_initial_task(
    task_id,
    email,
    filename,
    topic="",
    tags="",
    category="",
    meeting_date="",
    preset_id="",
    context_json="",
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO records (
                id, email, filename, status, created_at,
                topic, tags, category, meeting_date, preset_id, context_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                email,
                filename,
                "pending",
                datetime.now(),
                topic or "",
                tags or "",
                category or "",
                meeting_date or "",
                preset_id or "",
                context_json or "",
            ),
        )


def update_record(task_id, status=None, transcript=None, summary=None):
    with sqlite3.connect(DB_PATH) as conn:
        if status is not None:
            conn.execute("UPDATE records SET status=? WHERE id=?", (status, task_id))
        if transcript is not None:
            conn.execute("UPDATE records SET transcript=? WHERE id=?", (transcript, task_id))
        if summary is not None:
            conn.execute("UPDATE records SET summary=? WHERE id=?", (summary, task_id))


def get_recent_records(days=7, search="", category="", status_filter=""):
    """
    status_filter: '' | 'completed' | 'error' | 'processing'
    """
    limit = datetime.now() - timedelta(days=days)
    q = (search or "").strip()
    cat = (category or "").strip()
    sf = (status_filter or "").strip()

    clauses = ["created_at > ?"]
    params = [limit]

    if q:
        like = f"%{q}%"
        clauses.append("(topic LIKE ? OR filename LIKE ? OR tags LIKE ? OR summary LIKE ?)")
        params.extend([like, like, like, like])

    if cat:
        clauses.append("category = ?")
        params.append(cat)

    if sf == "completed":
        clauses.append("status = 'completed'")
    elif sf == "error":
        clauses.append("status LIKE 'Error%'")
    elif sf == "processing":
        clauses.append("(status = 'pending' OR status LIKE 'processing%')")

    where = " AND ".join(clauses)
    sql = f"SELECT * FROM records WHERE {where} ORDER BY created_at DESC"

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


def get_active_queue_records(days=7, limit=30):
    """処理待ち・実行中のタスク（ダッシュボード用）。"""
    since = datetime.now() - timedelta(days=days)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT * FROM records
            WHERE created_at > ?
              AND (status = 'pending' OR status LIKE 'processing%')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (since, limit),
        ).fetchall()


def get_record(task_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM records WHERE id = ?", (task_id,)).fetchone()


def parse_context_json(row):
    if not row:
        return {}
    try:
        raw = row["context_json"]
    except (KeyError, IndexError, TypeError):
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
