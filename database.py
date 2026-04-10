import glob
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional, Tuple

DATA_DIR = "data"
REGISTRY_DB_PATH = os.path.join(DATA_DIR, "registry.db")
LEGACY_MINUTES_PATH = os.path.join(DATA_DIR, "minutes.db")


def registry_login_normalize(s: str) -> str:
    """ログイン ID（メール）を DB 照合用に正規化（前後空白除去・小文字）。"""
    return (s or "").strip().lower()


def validate_registry_login_email(s: str) -> None:
    """users.username に格納するログイン ID としてのメール検証（registry_login_normalize 済みを想定）。"""
    if not s:
        raise ValueError("メールアドレスを入力してください")
    if len(s) > 254:
        raise ValueError("メールアドレスが長すぎます")
    if s.count("@") != 1:
        raise ValueError("メールアドレスの形式が正しくありません")
    local, domain = s.split("@", 1)
    if not local or not domain or len(local) > 64 or len(domain) > 253:
        raise ValueError("メールアドレスの形式が正しくありません")
    if "." not in domain:
        raise ValueError("メールアドレスの形式が正しくありません")
    if ".." in local or ".." in domain:
        raise ValueError("メールアドレスの形式が正しくありません")
    if local.startswith(".") or local.endswith(".") or domain.startswith(".") or domain.endswith("."):
        raise ValueError("メールアドレスの形式が正しくありません")


# 環境変数未設定・不正時の議事録保持日数（約1か月）。MM_MINUTES_RETENTION_DAYS で上書き可。
DEFAULT_MINUTES_RETENTION_DAYS = 30


def minutes_retention_days() -> int:
    """議事録の保持日数。MM_MINUTES_RETENTION_DAYS（未設定時は DEFAULT_MINUTES_RETENTION_DAYS）。0 以下で自動削除を無効。"""
    raw = (os.getenv("MM_MINUTES_RETENTION_DAYS") or "").strip()
    if not raw:
        return DEFAULT_MINUTES_RETENTION_DAYS
    try:
        n = int(raw, 10)
    except ValueError:
        return DEFAULT_MINUTES_RETENTION_DAYS
    # 旧既定 MM_MINUTES_RETENTION_DAYS=183（約半年）の .env が残っている環境を、現行の約1か月に揃える
    if n == 183:
        return DEFAULT_MINUTES_RETENTION_DAYS
    return n


def _auth_secret_configured() -> bool:
    return bool((os.getenv("MM_AUTH_SECRET") or "").strip())


def minutes_db_path(owner: str) -> str:
    o = (owner or "").strip()
    if not o:
        return LEGACY_MINUTES_PATH
    d = os.path.join(DATA_DIR, "user_data", _owner_slug(o))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "minutes.db")


def _owner_slug(username: str) -> str:
    raw = username.strip()
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in raw)[:40].strip("_") or "u"
    return f"{safe}_{h}"


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
        ("processing_started_at", "TIMESTAMP"),
        ("processing_finished_at", "TIMESTAMP"),
        ("audio_extract_only", "INTEGER NOT NULL DEFAULT 0"),
        ("transcript_only", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for col, decl in additions:
        if col not in existing:
            conn.execute(f"ALTER TABLE records ADD COLUMN {col} {decl}")


def _ensure_minutes_schema(conn):
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


def init_minutes_db(owner: str = ""):
    """議事録テーブル（ユーザー別ファイルまたは匿名は data/minutes.db）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = minutes_db_path(owner)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with sqlite3.connect(path) as conn:
        _ensure_minutes_schema(conn)


def _migrate_registry_user_columns(conn):
    cur = conn.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in cur.fetchall()}
    if "openai_api_key" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN openai_api_key TEXT DEFAULT ''")
    if "openai_model" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN openai_model TEXT DEFAULT ''")


def _migrate_registry_is_admin(conn):
    cur = conn.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in cur.fetchall()}
    if "is_admin" in cols:
        return
    conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")


def _migrate_usage_tables(conn):
    """管理者向け利用ログ（議事録本文は保存しない）。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_job_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            task_id TEXT NOT NULL UNIQUE,
            user_email TEXT NOT NULL DEFAULT '',
            transcript_only INTEGER NOT NULL,
            llm_provider TEXT NOT NULL,
            model_name TEXT NOT NULL DEFAULT '',
            whisper_preset TEXT NOT NULL DEFAULT 'accurate',
            media_kind TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_admin_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            author_email TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS suggestion_box_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            author_email TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL,
            page_url TEXT NOT NULL DEFAULT '',
            client_version TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            admin_note TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_job_log_created ON usage_job_log(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_suggestion_box_created ON suggestion_box_entries(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_suggestion_box_status ON suggestion_box_entries(status)")
    _migrate_usage_job_metrics_columns(conn)


def _migrate_usage_job_metrics_columns(conn):
    """稟議・キャパシティ用: 投入ファイルサイズ・媒体長・Whisper/LLM 壁時計など（本文は保存しない）。"""
    cur = conn.execute("PRAGMA table_info(usage_job_log)")
    existing = {row[1] for row in cur.fetchall()}
    additions = [
        ("input_bytes", "INTEGER"),
        ("media_duration_sec", "REAL"),
        ("audio_extract_wall_sec", "REAL"),
        ("whisper_wall_sec", "REAL"),
        ("transcript_chars", "INTEGER"),
        ("extract_llm_sec", "REAL"),
        ("merge_llm_sec", "REAL"),
        ("llm_chunks", "INTEGER"),
    ]
    for col, decl in additions:
        if col not in existing:
            conn.execute(f"ALTER TABLE usage_job_log ADD COLUMN {col} {decl}")


def _ensure_at_least_one_admin(conn):
    """管理者が 1 人もいなければ、最古のユーザーを管理者にする（レガシー移行直後など）。"""
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        return
    if conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0] > 0:
        return
    conn.execute("UPDATE users SET is_admin = 1 WHERE rowid = (SELECT MIN(rowid) FROM users)")


def _try_bootstrap_admin_registry(conn):
    user = (os.getenv("MM_BOOTSTRAP_ADMIN_USER") or "").strip()
    raw_pw = (os.getenv("MM_BOOTSTRAP_ADMIN_PASSWORD") or "").strip()
    if not user or not raw_pw:
        return
    user_key = registry_login_normalize(user)
    try:
        validate_registry_login_email(user_key)
    except ValueError:
        return
    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if n > 0:
        return
    try:
        import bcrypt
    except ImportError:
        return
    h = bcrypt.hashpw(raw_pw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    conn.execute(
        "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
        (user_key, h),
    )


def _maybe_migrate_legacy_users_to_registry():
    if not os.path.exists(LEGACY_MINUTES_PATH):
        return
    with sqlite3.connect(REGISTRY_DB_PATH) as reg:
        n = reg.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if n > 0:
            return
    try:
        with sqlite3.connect(REGISTRY_DB_PATH) as reg:
            reg.execute("ATTACH DATABASE ? AS legacy", (LEGACY_MINUTES_PATH,))
            reg.execute(
                """
                INSERT OR IGNORE INTO users (username, password_hash, created_at)
                SELECT username, password_hash, created_at FROM legacy.users
                WHERE EXISTS (
                    SELECT 1 FROM legacy.sqlite_master
                    WHERE type='table' AND name='users'
                )
                """
            )
            reg.execute("DETACH DATABASE legacy")
    except sqlite3.OperationalError:
        pass


def init_registry_db():
    if not _auth_secret_configured():
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_admin INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        _migrate_registry_user_columns(conn)
        _migrate_registry_is_admin(conn)
    # レガシー data/minutes.db の users を先に移す（後からブートストラップすると移行がスキップされ、旧パスワードで通らなくなる）
    _maybe_migrate_legacy_users_to_registry()
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        _ensure_at_least_one_admin(conn)
        _try_bootstrap_admin_registry(conn)
        _migrate_usage_tables(conn)


def init_db():
    init_registry_db()
    init_minutes_db("")


def count_users() -> int:
    if not _auth_secret_configured() or not os.path.exists(REGISTRY_DB_PATH):
        return 0
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        return int(row[0]) if row else 0


def get_user_by_username(username: str):
    raw = (username or "").strip()
    if not raw or not os.path.exists(REGISTRY_DB_PATH):
        return None
    key = registry_login_normalize(raw)
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE username = ?", (key,)).fetchone()
        if row is None and key != raw:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (raw,)).fetchone()
        return row


def resolve_registry_username_for_mutation(login_id: str) -> Optional[str]:
    """URL や入力から実際の users.username（主キー）を解決する（レガシー表記の大文字小文字差を吸収）。"""
    row = get_user_by_username(login_id)
    if not row:
        return None
    return str(row["username"])


def get_user_openai_settings(username: str) -> Tuple[Optional[str], str]:
    row = get_user_by_username(username)
    if not row:
        return None, "gpt-4o-mini"
    try:
        key = (row["openai_api_key"] or "").strip() or None
    except (KeyError, IndexError, TypeError):
        key = None
    try:
        model = (row["openai_model"] or "").strip() or "gpt-4o-mini"
    except (KeyError, IndexError, TypeError):
        model = "gpt-4o-mini"
    return key, model


def update_user_openai(username: str, api_key: Optional[str] = None, model: Optional[str] = None):
    u = (username or "").strip()
    if not u:
        return
    init_registry_db()
    if not os.path.exists(REGISTRY_DB_PATH):
        return
    sets: list[str] = []
    params: list[Any] = []
    if api_key is not None:
        sets.append("openai_api_key = ?")
        params.append((api_key or "").strip())
    if model is not None:
        sets.append("openai_model = ?")
        params.append((model or "").strip() or "gpt-4o-mini")
    if not sets:
        return
    params.append(u)
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE username = ?", params)


def user_is_admin(username: str) -> bool:
    row = get_user_by_username(username)
    if not row:
        return False
    try:
        return int(row["is_admin"] or 0) == 1
    except (KeyError, TypeError, ValueError):
        return False


def count_admins() -> int:
    if not os.path.exists(REGISTRY_DB_PATH):
        return 0
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        row = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()
        return int(row[0]) if row else 0


def list_registry_users() -> list[dict[str, Any]]:
    if not _auth_secret_configured() or not os.path.exists(REGISTRY_DB_PATH):
        return []
    init_registry_db()
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT username, is_admin, created_at FROM users ORDER BY datetime(created_at) ASC"
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "username": r["username"],
                "is_admin": bool(int(r["is_admin"] or 0)),
                "created_at": r["created_at"],
            }
        )
    return out


def list_admin_emails() -> list[str]:
    """管理者ユーザーのログイン名（メールアドレス形式）一覧。エラー報告の宛先に使う。"""
    return [u["username"] for u in list_registry_users() if u.get("is_admin")]


def bootstrap_registry_admin(username: str, password: str) -> None:
    """認証 DB にユーザーが 0 件のときだけ最初の管理者を 1 人登録する（並行リクエスト対策で IMMEDIATE ロック）。"""
    import bcrypt

    u = registry_login_normalize(username)
    validate_registry_login_email(u)
    raw_pw = (password or "").replace("\r", "")
    if len(raw_pw) < 8:
        raise ValueError("パスワードは 8 文字以上にしてください")
    h = bcrypt.hashpw(raw_pw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    init_registry_db()
    conn = sqlite3.connect(REGISTRY_DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if n > 0:
            conn.rollback()
            raise ValueError("初期設定は既に完了しています")
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
            (u, h),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_registry_user(username: str, password: str, *, is_admin: bool = False) -> None:
    import bcrypt

    u = registry_login_normalize(username)
    validate_registry_login_email(u)
    raw_pw = (password or "").replace("\r", "")
    if len(raw_pw) < 8:
        raise ValueError("パスワードは 8 文字以上にしてください")
    h = bcrypt.hashpw(raw_pw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    init_registry_db()
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (u, h, 1 if is_admin else 0),
        )


def set_registry_user_password(username: str, new_password: str) -> bool:
    import bcrypt

    u = (username or "").strip()
    if not u:
        return False
    raw_pw = (new_password or "").replace("\r", "")
    if len(raw_pw) < 8:
        raise ValueError("パスワードは 8 文字以上にしてください")
    h = bcrypt.hashpw(raw_pw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    init_registry_db()
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        cur = conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (h, u))
        return cur.rowcount > 0


def set_registry_user_admin(username: str, is_admin: bool) -> None:
    u = (username or "").strip()
    if not u:
        raise ValueError("ユーザー名が不正です")
    if not get_user_by_username(u):
        raise KeyError("not found")
    if not is_admin and user_is_admin(u) and count_admins() <= 1:
        raise ValueError("最後の管理者権限は外せません")
    init_registry_db()
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.execute("UPDATE users SET is_admin = ? WHERE username = ?", (1 if is_admin else 0, u))


def delete_registry_user(username: str) -> None:
    u = (username or "").strip()
    if not u:
        raise ValueError("ユーザー名が不正です")
    row = get_user_by_username(u)
    if not row:
        raise KeyError("not found")
    if user_is_admin(u) and count_admins() <= 1:
        raise ValueError("最後の管理者は削除できません")
    init_registry_db()
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.execute("DELETE FROM users WHERE username = ?", (u,))


def save_initial_task(
    task_id,
    email,
    filename,
    owner: str = "",
    topic="",
    tags="",
    category="",
    meeting_date="",
    preset_id="",
    context_json="",
    transcript_only: bool = False,
):
    init_minutes_db(owner)
    path = minutes_db_path(owner)
    purge_expired_minutes_db_path(path)
    with sqlite3.connect(path) as conn:
        _ensure_minutes_schema(conn)
        conn.execute(
            """
            INSERT INTO records (
                id, email, filename, status, created_at,
                topic, tags, category, meeting_date, preset_id, context_json, transcript_only
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if transcript_only else 0,
            ),
        )


def update_record(task_id, owner: str = "", status=None, transcript=None, summary=None):
    path = minutes_db_path(owner)
    with sqlite3.connect(path) as conn:
        _ensure_minutes_schema(conn)
        if status is not None:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT status FROM records WHERE id=?", (task_id,)).fetchone()
            if row:
                old_status = row["status"] or ""
                now = datetime.now()
                if old_status == "pending" and status != "pending":
                    conn.execute(
                        "UPDATE records SET processing_started_at = COALESCE(processing_started_at, ?) WHERE id=?",
                        (now, task_id),
                    )
                if status == "completed" or status == "cancelled" or (
                    isinstance(status, str) and status.startswith("Error")
                ):
                    conn.execute(
                        "UPDATE records SET processing_finished_at = ? WHERE id=?",
                        (now, task_id),
                    )
            conn.execute("UPDATE records SET status=? WHERE id=?", (status, task_id))
        if transcript is not None:
            conn.execute("UPDATE records SET transcript=? WHERE id=?", (transcript, task_id))
        if summary is not None:
            conn.execute("UPDATE records SET summary=? WHERE id=?", (summary, task_id))


def remove_task_upload_files(task_id: str) -> None:
    """API 破棄時: downloads 直下の {task_id}_* を削除。"""
    pattern = os.path.join("downloads", f"{task_id}_*")
    for p in glob.glob(pattern):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def cleanup_user_prompts_dir(task_id: str) -> None:
    d = os.path.join(DATA_DIR, "user_prompts", task_id)
    try:
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def purge_expired_minutes_db_path(path: str) -> int:
    """minutes.db 1 ファイル単位で、保持期限を過ぎたレコードと関連ファイルを削除。戻り値は削除した行数。"""
    d = minutes_retention_days()
    if d <= 0 or not path or not os.path.isfile(path):
        return 0
    cutoff = datetime.now() - timedelta(days=d)
    try:
        with sqlite3.connect(path) as conn:
            _ensure_minutes_schema(conn)
            cur = conn.execute(
                """
                SELECT id FROM records
                WHERE created_at IS NOT NULL
                  AND created_at < ?
                  AND NOT (status = 'pending' OR status LIKE 'processing%')
                """,
                (cutoff,),
            )
            ids = [row[0] for row in cur.fetchall()]
            for tid in ids:
                remove_task_upload_files(tid)
                cleanup_user_prompts_dir(tid)
            if ids:
                conn.executemany("DELETE FROM records WHERE id = ?", [(i,) for i in ids])
        return len(ids)
    except sqlite3.Error:
        return 0


def purge_expired_minutes(owner: str = "") -> int:
    init_minutes_db(owner)
    return purge_expired_minutes_db_path(minutes_db_path(owner))


def purge_all_minutes_archives() -> int:
    """全ユーザー分＋レガシー data/minutes.db をスキャンして期限切れを削除。戻り値は削除したレコード件数の合計。"""
    total = 0
    if os.path.isfile(LEGACY_MINUTES_PATH):
        total += purge_expired_minutes_db_path(LEGACY_MINUTES_PATH)
    base = os.path.join(DATA_DIR, "user_data")
    if os.path.isdir(base):
        for name in os.listdir(base):
            p = os.path.join(base, name, "minutes.db")
            if os.path.isfile(p):
                total += purge_expired_minutes_db_path(p)
    return total


def discard_task(task_id: str, owner: str = "") -> None:
    """pending / processing* / Error 終了を cancelled にする。完了・破棄済みは不可。Error の場合は理由を summary に残す。"""
    row = get_record(task_id, owner or "")
    if not row:
        raise KeyError(task_id)
    st = (row["status"] or "").strip()
    if st in ("completed", "cancelled"):
        raise ValueError("すでに終了しているため破棄できません")
    if st.startswith("Error"):
        err_detail = st[6:].strip() if st.lower().startswith("error:") else st
        update_record(task_id, owner or "", status="cancelled", summary=f"【処理エラー】\n{err_detail}")
        return
    if st != "pending" and not st.startswith("processing"):
        raise ValueError("破棄できない状態です")
    update_record(task_id, owner or "", status="cancelled")


def _recent_records_where_clause(
    days: int,
    search: str,
    category: str,
    status_filter: str,
) -> tuple[str, list[Any]]:
    limit_dt = datetime.now() - timedelta(days=days)
    q = (search or "").strip()
    cat = (category or "").strip()
    sf = (status_filter or "").strip()

    clauses = ["created_at > ?"]
    params: list[Any] = [limit_dt]

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
        clauses.append(
            "(status LIKE 'Error%' OR (status = 'cancelled' AND summary LIKE '【処理エラー】%'))"
        )
    elif sf == "cancelled":
        clauses.append("status = 'cancelled'")
    elif sf == "processing":
        clauses.append("(status = 'pending' OR status LIKE 'processing%')")

    return " AND ".join(clauses), params


def count_recent_records(
    owner: str = "",
    days=7,
    search="",
    category="",
    status_filter="",
) -> int:
    init_minutes_db(owner)
    path = minutes_db_path(owner)
    purge_expired_minutes_db_path(path)
    where, params = _recent_records_where_clause(days, search, category, status_filter)
    sql = f"SELECT COUNT(*) FROM records WHERE {where}"
    with sqlite3.connect(path) as conn:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0


def get_recent_records(
    owner: str = "",
    days=7,
    search="",
    category="",
    status_filter="",
    limit: Optional[int] = None,
    offset: int = 0,
):
    init_minutes_db(owner)
    path = minutes_db_path(owner)
    purge_expired_minutes_db_path(path)
    where, params = _recent_records_where_clause(days, search, category, status_filter)
    sql = f"SELECT * FROM records WHERE {where} ORDER BY created_at DESC"
    qparams: list[Any] = list(params)
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        qparams.extend([limit, max(0, int(offset))])

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, qparams).fetchall()


def get_active_queue_records(owner: str = "", days=7, limit=30):
    init_minutes_db(owner)
    purge_expired_minutes_db_path(minutes_db_path(owner))
    since = datetime.now() - timedelta(days=days)
    path = minutes_db_path(owner)
    with sqlite3.connect(path) as conn:
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


def _queue_rows_from_minutes_path(path: str, since: datetime) -> list[sqlite3.Row]:
    if not os.path.isfile(path):
        return []
    purge_expired_minutes_db_path(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT * FROM records
            WHERE created_at > ?
              AND (status = 'pending' OR status LIKE 'processing%')
            ORDER BY created_at ASC
            """,
            (since,),
        ).fetchall()


def get_active_queue_records_global(viewer: str = "", days: int = 7, limit: int = 80) -> list[dict[str, Any]]:
    """認証あり運用向け: 全登録ユーザーの DB・レガシー `data/minutes.db`・registry 外の `user_data/*/minutes.db` を走査し、
    待機・処理中を受付時刻順でマージする。各 dict はレコード列に加え `job_owner`（ログイン ID・レガシーは null）と `is_mine` を含む。
    """
    since = datetime.now() - timedelta(days=days)
    viewer_n = registry_login_normalize(viewer) if (viewer or "").strip() else ""

    pairs: list[tuple[str, sqlite3.Row]] = []
    registry_paths_norm: set[str] = set()

    for u in list_registry_users():
        un = (u.get("username") or "").strip()
        if not un:
            continue
        path = minutes_db_path(un)
        registry_paths_norm.add(os.path.normpath(path))
        for r in _queue_rows_from_minutes_path(path, since):
            pairs.append((un, r))

    pattern = os.path.join(DATA_DIR, "user_data", "*", "minutes.db")
    for path in glob.glob(pattern):
        norm = os.path.normpath(path)
        if norm in registry_paths_norm:
            continue
        label = os.path.basename(os.path.dirname(path))
        for r in _queue_rows_from_minutes_path(path, since):
            pairs.append((label, r))

    leg_norm = os.path.normpath(LEGACY_MINUTES_PATH)
    if os.path.isfile(LEGACY_MINUTES_PATH) and leg_norm not in registry_paths_norm:
        for r in _queue_rows_from_minutes_path(LEGACY_MINUTES_PATH, since):
            pairs.append(("", r))

    pairs.sort(key=lambda pr: (str(pr[1]["created_at"] or ""), pr[1]["id"]))

    out: list[dict[str, Any]] = []
    for owner_label, row in pairs[:limit]:
        d: dict[str, Any] = {k: row[k] for k in row.keys()}
        d["job_owner"] = owner_label if owner_label else None
        if viewer_n:
            if owner_label:
                d["is_mine"] = registry_login_normalize(owner_label) == viewer_n
            else:
                d["is_mine"] = registry_login_normalize(str(d.get("email") or "")) == viewer_n
        else:
            d["is_mine"] = True
        out.append(d)
    return out


def get_record(task_id, owner: str = ""):
    path = minutes_db_path(owner)
    if not os.path.exists(path):
        return None
    with sqlite3.connect(path) as conn:
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


def usage_media_kind_from_filename(filename: str) -> str:
    """拡張子のみから媒体種別（ファイル名全文はログに保存しない）。"""
    base = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in base:
        return "other"
    ext = base.rsplit(".", 1)[-1].lower()
    if ext in ("mp4", "mov", "webm", "mkv", "avi"):
        return "video"
    if ext in ("mp3", "m4a", "wav", "flac", "ogg", "aac"):
        return "audio"
    if ext == "srt":
        return "srt"
    if ext == "txt":
        return "txt"
    return "other"


def record_usage_job_submission(
    task_id: str,
    user_email: str,
    transcript_only: bool,
    llm_provider: str,
    model_name: str,
    whisper_preset: str = "accurate",
    original_filename: str = "",
    input_bytes: Optional[int] = None,
) -> None:
    """認証が有効なときのみ registry に 1 行記録（タスク投入時）。議事録・書き起こし本文は含めない。"""
    if not _auth_secret_configured():
        return
    init_registry_db()
    if not os.path.exists(REGISTRY_DB_PATH):
        return
    tid = (task_id or "").strip()
    if not tid:
        return
    user_key = registry_login_normalize(user_email) if (user_email or "").strip() else ""
    prov = (llm_provider or "").strip().lower()
    if prov not in ("ollama", "openai"):
        prov = "ollama"
    model = (model_name or "").strip()
    if not model:
        model = "gpt-4o-mini" if prov == "openai" else ""
    wp = (whisper_preset or "accurate").strip() or "accurate"
    mk = usage_media_kind_from_filename(original_filename)
    ib = int(input_bytes) if input_bytes is not None and int(input_bytes) >= 0 else None
    try:
        with sqlite3.connect(REGISTRY_DB_PATH) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO usage_job_log
                (task_id, user_email, transcript_only, llm_provider, model_name, whisper_preset, media_kind, input_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tid, user_key, 1 if transcript_only else 0, prov, model, wp, mk, ib),
            )
    except sqlite3.OperationalError:
        pass


def update_usage_job_metrics(
    task_id: str,
    *,
    input_bytes: Optional[int] = None,
    media_duration_sec: Optional[float] = None,
    audio_extract_wall_sec: Optional[float] = None,
    whisper_wall_sec: Optional[float] = None,
    transcript_chars: Optional[int] = None,
    extract_llm_sec: Optional[float] = None,
    merge_llm_sec: Optional[float] = None,
    llm_chunks: Optional[int] = None,
) -> None:
    """ジョブ完了時に 1 回更新（認証・registry 有効時のみ）。失敗ジョブは呼ばなくてよい。"""
    if not _auth_secret_configured():
        return
    tid = (task_id or "").strip()
    if not tid:
        return
    init_registry_db()
    if not os.path.exists(REGISTRY_DB_PATH):
        return
    fields: list[tuple[str, Any]] = []
    if input_bytes is not None:
        fields.append(("input_bytes", int(input_bytes)))
    if media_duration_sec is not None:
        fields.append(("media_duration_sec", float(media_duration_sec)))
    if audio_extract_wall_sec is not None:
        fields.append(("audio_extract_wall_sec", float(audio_extract_wall_sec)))
    if whisper_wall_sec is not None:
        fields.append(("whisper_wall_sec", float(whisper_wall_sec)))
    if transcript_chars is not None:
        fields.append(("transcript_chars", int(transcript_chars)))
    if extract_llm_sec is not None:
        fields.append(("extract_llm_sec", float(extract_llm_sec)))
    if merge_llm_sec is not None:
        fields.append(("merge_llm_sec", float(merge_llm_sec)))
    if llm_chunks is not None:
        fields.append(("llm_chunks", int(llm_chunks)))
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k, _ in fields)
    vals = [v for _, v in fields] + [tid]
    try:
        with sqlite3.connect(REGISTRY_DB_PATH) as conn:
            conn.execute(f"UPDATE usage_job_log SET {sets} WHERE task_id = ?", vals)
    except sqlite3.OperationalError:
        pass


def _usage_pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(1000.0 * part / total) / 10.0


def _empty_metrics_rollup() -> dict[str, Any]:
    return {
        "jobs_with_metrics": 0,
        "sum_input_bytes": 0,
        "avg_input_bytes": 0.0,
        "sum_media_duration_sec": 0.0,
        "avg_media_duration_sec": 0.0,
        "sum_audio_extract_sec": 0.0,
        "avg_audio_extract_sec": 0.0,
        "sum_whisper_sec": 0.0,
        "avg_whisper_sec": 0.0,
        "sum_transcript_chars": 0,
        "avg_transcript_chars": 0.0,
        "sum_extract_llm_sec": 0.0,
        "sum_merge_llm_sec": 0.0,
        "sum_llm_sec": 0.0,
        "sum_llm_chunks": 0,
    }


def admin_usage_summary(days: int) -> dict[str, Any]:
    """管理者向け集計。直近 days 日の投入ログ（期間は最大 365 日）。"""
    days = max(1, min(365, int(days)))
    if not _auth_secret_configured() or not os.path.exists(REGISTRY_DB_PATH):
        return {
            "period_days": days,
            "total_submissions": 0,
            "pipeline_minutes_llm": {"count": 0, "pct": 0.0},
            "pipeline_transcript_only": {"count": 0, "pct": 0.0},
            "provider_ollama": {"count": 0, "pct": 0.0},
            "provider_openai": {"count": 0, "pct": 0.0},
            "ollama_models_for_llm_jobs": [],
            "openai_models_for_llm_jobs": [],
            "whisper_presets_for_media": [],
            "media_kind_breakdown": [],
            "metrics_rollup": _empty_metrics_rollup(),
        }
    init_registry_db()
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_s = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT transcript_only, llm_provider, model_name, whisper_preset, media_kind
            FROM usage_job_log
            WHERE created_at >= ?
            """,
            (cutoff_s,),
        ).fetchall()
    total = len(rows)
    n_llm = sum(1 for r in rows if not r[0])
    n_tr = total - n_llm
    n_ollama = sum(1 for r in rows if (r[1] or "").lower() == "ollama")
    n_openai = sum(1 for r in rows if (r[1] or "").lower() == "openai")

    ollama_llm_counts: dict[str, int] = {}
    openai_llm_counts: dict[str, int] = {}
    n_ollama_llm = 0
    n_openai_llm = 0
    for r in rows:
        if r[0]:
            continue
        prov = (r[1] or "").lower()
        m = (r[2] or "").strip() or "(未指定)"
        if prov == "ollama":
            n_ollama_llm += 1
            ollama_llm_counts[m] = ollama_llm_counts.get(m, 0) + 1
        elif prov == "openai":
            n_openai_llm += 1
            openai_llm_counts[m] = openai_llm_counts.get(m, 0) + 1
    ollama_models_list = sorted(ollama_llm_counts.items(), key=lambda x: (-x[1], x[0]))
    openai_models_list = sorted(openai_llm_counts.items(), key=lambda x: (-x[1], x[0]))
    ollama_models_for_llm = [
        {"model": name, "count": c, "pct": _usage_pct(c, n_ollama_llm)} for name, c in ollama_models_list
    ]
    openai_models_for_llm = [
        {"model": name, "count": c, "pct": _usage_pct(c, n_openai_llm)} for name, c in openai_models_list
    ]

    whisper_rows = [r for r in rows if r[4] in ("video", "audio")]
    wtotal = len(whisper_rows)
    wp_counts: dict[str, int] = {}
    for r in whisper_rows:
        w = (r[3] or "accurate").strip() or "accurate"
        wp_counts[w] = wp_counts.get(w, 0) + 1
    wp_list = sorted(wp_counts.items(), key=lambda x: (-x[1], x[0]))
    whisper_presets = [
        {"preset": p, "count": c, "pct": _usage_pct(c, wtotal)} for p, c in wp_list
    ]

    mk_counts: dict[str, int] = {}
    for r in rows:
        k = r[4] or "other"
        mk_counts[k] = mk_counts.get(k, 0) + 1
    mk_list = sorted(mk_counts.items(), key=lambda x: (-x[1], x[0]))
    media_breakdown = [{"kind": k, "count": c, "pct": _usage_pct(c, total)} for k, c in mk_list]

    metrics_rollup = _empty_metrics_rollup()
    try:
        with sqlite3.connect(REGISTRY_DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT
                  COUNT(*),
                  COALESCE(SUM(input_bytes), 0),
                  COALESCE(AVG(input_bytes), 0),
                  COALESCE(SUM(media_duration_sec), 0),
                  COALESCE(AVG(media_duration_sec), 0),
                  COALESCE(SUM(audio_extract_wall_sec), 0),
                  COALESCE(AVG(audio_extract_wall_sec), 0),
                  COALESCE(SUM(whisper_wall_sec), 0),
                  COALESCE(AVG(whisper_wall_sec), 0),
                  COALESCE(SUM(transcript_chars), 0),
                  COALESCE(AVG(transcript_chars), 0),
                  COALESCE(SUM(extract_llm_sec), 0),
                  COALESCE(SUM(merge_llm_sec), 0),
                  COALESCE(SUM(llm_chunks), 0)
                FROM usage_job_log
                WHERE created_at >= ? AND transcript_chars IS NOT NULL
                """,
                (cutoff_s,),
            ).fetchone()
        if row and int(row[0] or 0) > 0:
            n_m = int(row[0])
            sum_ex = float(row[11] or 0) + float(row[12] or 0)
            metrics_rollup = {
                "jobs_with_metrics": n_m,
                "sum_input_bytes": int(row[1] or 0),
                "avg_input_bytes": round(float(row[2] or 0), 2),
                "sum_media_duration_sec": round(float(row[3] or 0), 3),
                "avg_media_duration_sec": round(float(row[4] or 0), 3),
                "sum_audio_extract_sec": round(float(row[5] or 0), 3),
                "avg_audio_extract_sec": round(float(row[6] or 0), 3),
                "sum_whisper_sec": round(float(row[7] or 0), 3),
                "avg_whisper_sec": round(float(row[8] or 0), 3),
                "sum_transcript_chars": int(row[9] or 0),
                "avg_transcript_chars": round(float(row[10] or 0), 1),
                "sum_extract_llm_sec": round(float(row[11] or 0), 3),
                "sum_merge_llm_sec": round(float(row[12] or 0), 3),
                "sum_llm_sec": round(sum_ex, 3),
                "sum_llm_chunks": int(row[13] or 0),
            }
    except (sqlite3.OperationalError, TypeError, ValueError):
        pass

    return {
        "period_days": days,
        "total_submissions": total,
        "pipeline_minutes_llm": {"count": n_llm, "pct": _usage_pct(n_llm, total)},
        "pipeline_transcript_only": {"count": n_tr, "pct": _usage_pct(n_tr, total)},
        "provider_ollama": {"count": n_ollama, "pct": _usage_pct(n_ollama, total)},
        "provider_openai": {"count": n_openai, "pct": _usage_pct(n_openai, total)},
        "ollama_models_for_llm_jobs": ollama_models_for_llm,
        "openai_models_for_llm_jobs": openai_models_for_llm,
        "whisper_presets_for_media": whisper_presets,
        "media_kind_breakdown": media_breakdown,
        "metrics_rollup": metrics_rollup,
    }


def admin_usage_events(days: int, limit: int = 100, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """直近のイベント一覧と総件数（ページング用）。期間は最大 365 日。"""
    days = max(1, min(365, int(days)))
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    if not _auth_secret_configured() or not os.path.exists(REGISTRY_DB_PATH):
        return [], 0
    init_registry_db()
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_s = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        total = int(
            conn.execute(
                "SELECT COUNT(*) FROM usage_job_log WHERE created_at >= ?",
                (cutoff_s,),
            ).fetchone()[0]
        )
        cur = conn.execute(
            """
            SELECT id, created_at, task_id, user_email, transcript_only, llm_provider, model_name,
                   whisper_preset, media_kind,
                   input_bytes, media_duration_sec, audio_extract_wall_sec, whisper_wall_sec,
                   transcript_chars, extract_llm_sec, merge_llm_sec, llm_chunks
            FROM usage_job_log
            WHERE created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (cutoff_s, limit, offset),
        )
        items = []
        for row in cur.fetchall():
            items.append(
                {
                    "id": row["id"],
                    "created_at": str(row["created_at"]) if row["created_at"] is not None else "",
                    "task_id": row["task_id"],
                    "user_email": row["user_email"] or "",
                    "transcript_only": bool(row["transcript_only"]),
                    "llm_provider": row["llm_provider"],
                    "model_name": row["model_name"] or "",
                    "whisper_preset": row["whisper_preset"] or "",
                    "media_kind": row["media_kind"] or "",
                    "input_bytes": row["input_bytes"],
                    "media_duration_sec": row["media_duration_sec"],
                    "audio_extract_wall_sec": row["audio_extract_wall_sec"],
                    "whisper_wall_sec": row["whisper_wall_sec"],
                    "transcript_chars": row["transcript_chars"],
                    "extract_llm_sec": row["extract_llm_sec"],
                    "merge_llm_sec": row["merge_llm_sec"],
                    "llm_chunks": row["llm_chunks"],
                }
            )
    return items, total


def usage_admin_notes_list(limit: int = 80) -> list[dict[str, Any]]:
    limit = max(1, min(200, int(limit)))
    if not _auth_secret_configured() or not os.path.exists(REGISTRY_DB_PATH):
        return []
    init_registry_db()
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id, created_at, author_email, body
            FROM usage_admin_notes
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "id": r["id"],
                "created_at": str(r["created_at"]) if r["created_at"] is not None else "",
                "author_email": r["author_email"] or "",
                "body": r["body"] or "",
            }
            for r in cur.fetchall()
        ]


def usage_admin_note_get(note_id: int) -> Optional[dict[str, Any]]:
    if not _auth_secret_configured() or not os.path.exists(REGISTRY_DB_PATH):
        return None
    init_registry_db()
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute(
            """
            SELECT id, created_at, author_email, body
            FROM usage_admin_notes
            WHERE id = ?
            """,
            (int(note_id),),
        ).fetchone()
        if r is None:
            return None
        return {
            "id": int(r["id"]),
            "created_at": str(r["created_at"]) if r["created_at"] is not None else "",
            "author_email": r["author_email"] or "",
            "body": r["body"] or "",
        }


def usage_admin_note_add(author_email: str, body: str) -> Optional[int]:
    text = (body or "").strip()
    if not text:
        return None
    if not _auth_secret_configured():
        return None
    init_registry_db()
    auth = registry_login_normalize(author_email) if (author_email or "").strip() else ""
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO usage_admin_notes (author_email, body) VALUES (?, ?)",
            (auth, text),
        )
        return int(cur.lastrowid) if cur.lastrowid is not None else None


def usage_admin_note_delete(note_id: int) -> bool:
    if not _auth_secret_configured():
        return False
    init_registry_db()
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        cur = conn.execute("DELETE FROM usage_admin_notes WHERE id = ?", (int(note_id),))
        return cur.rowcount > 0


def _normalize_suggestion_status(value: str) -> str:
    v = (value or "").strip().lower()
    if v not in ("new", "in_progress", "done"):
        return "new"
    return v


def _suggestion_box_row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "created_at": str(r["created_at"]) if r["created_at"] is not None else "",
        "updated_at": str(r["updated_at"]) if r["updated_at"] is not None else "",
        "author_email": r["author_email"] or "",
        "subject": r["subject"] or "",
        "body": r["body"] or "",
        "page_url": r["page_url"] or "",
        "client_version": r["client_version"] or "",
        "status": _normalize_suggestion_status(r["status"] or "new"),
        "admin_note": r["admin_note"] or "",
    }


def suggestion_box_create(
    author_email: str,
    subject: str,
    body: str,
    page_url: str = "",
    client_version: str = "",
) -> Optional[int]:
    """目安箱を 1 件保存（認証・registry 有効時）。"""
    text = (body or "").strip()
    if not text or not _auth_secret_configured():
        return None
    init_registry_db()
    auth = registry_login_normalize(author_email) if (author_email or "").strip() else ""
    subj = (subject or "").strip()[:200]
    page = (page_url or "").strip()[:2000]
    ver = (client_version or "").strip()[:64]
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO suggestion_box_entries
            (author_email, subject, body, page_url, client_version, status)
            VALUES (?, ?, ?, ?, ?, 'new')
            """,
            (auth, subj, text[:8000], page, ver),
        )
        return int(cur.lastrowid) if cur.lastrowid is not None else None


def suggestion_box_admin_list(
    *,
    status: str = "",
    limit: int = 80,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    if not _auth_secret_configured() or not os.path.exists(REGISTRY_DB_PATH):
        return [], 0
    init_registry_db()
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    st = (status or "").strip().lower()
    where = ""
    params_count: list[Any] = []
    params_list: list[Any] = []
    if st in ("new", "in_progress", "done"):
        where = "WHERE status = ?"
        params_count.append(st)
        params_list.append(st)
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM suggestion_box_entries {where}",
                tuple(params_count),
            ).fetchone()[0]
        )
        cur = conn.execute(
            f"""
            SELECT id, created_at, updated_at, author_email, subject, body, page_url, client_version, status, admin_note
            FROM suggestion_box_entries
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params_list + [limit, offset]),
        )
        return [_suggestion_box_row_to_dict(r) for r in cur.fetchall()], total


def suggestion_box_admin_update(
    ticket_id: int,
    *,
    status: Optional[str] = None,
    admin_note: Optional[str] = None,
) -> bool:
    if not _auth_secret_configured() or not os.path.exists(REGISTRY_DB_PATH):
        return False
    fields: list[tuple[str, Any]] = []
    if status is not None:
        fields.append(("status", _normalize_suggestion_status(status)))
    if admin_note is not None:
        fields.append(("admin_note", (admin_note or "")[:8000]))
    if not fields:
        return False
    sets = ", ".join([f"{k} = ?" for k, _ in fields] + ["updated_at = CURRENT_TIMESTAMP"])
    vals = [v for _, v in fields] + [int(ticket_id)]
    init_registry_db()
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        cur = conn.execute(
            f"UPDATE suggestion_box_entries SET {sets} WHERE id = ?",
            vals,
        )
        return cur.rowcount > 0


def suggestion_box_admin_get(ticket_id: int) -> Optional[dict[str, Any]]:
    if not _auth_secret_configured() or not os.path.exists(REGISTRY_DB_PATH):
        return None
    init_registry_db()
    with sqlite3.connect(REGISTRY_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute(
            """
            SELECT id, created_at, updated_at, author_email, subject, body, page_url, client_version, status, admin_note
            FROM suggestion_box_entries
            WHERE id = ?
            """,
            (int(ticket_id),),
        ).fetchone()
        if r is None:
            return None
        return _suggestion_box_row_to_dict(r)
