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


def get_recent_records(
    owner: str = "",
    days=7,
    search="",
    category="",
    status_filter="",
):
    init_minutes_db(owner)
    purge_expired_minutes_db_path(minutes_db_path(owner))
    limit = datetime.now() - timedelta(days=days)
    q = (search or "").strip()
    cat = (category or "").strip()
    sf = (status_filter or "").strip()

    clauses = ["created_at > ?"]
    params: list[Any] = [limit]

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

    where = " AND ".join(clauses)
    sql = f"SELECT * FROM records WHERE {where} ORDER BY created_at DESC"

    path = minutes_db_path(owner)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


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
