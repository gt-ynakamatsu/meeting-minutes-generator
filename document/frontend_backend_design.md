# フロントエンド／バックエンド分離 設計書

## 1. 目的とスコープ

- **目的**: UI（フロントエンド）と HTTP API・ジョブ投入（バックエンド）を分離し、開発・デプロイ・スケールの単位を明確にする。
- **スコープ**: 既存の Celery ワーカー（GPU・Whisper・LLM 処理）、SQLite（`database.py`）、プロンプト資産は維持し、**画面と REST API を追加**する。
- **非スコープ（現時点）**: 多テナントの厳密分離以外の大規模 IAM、S3 等へのストレージ移行、PostgreSQL 化。

---

## 2. 論理アーキテクチャ

```mermaid
flowchart LR
  subgraph client["ブラウザ"]
    FE["React SPA"]
  end
  subgraph edge["エッジ"]
    NG["Nginx 8085"]
  end
  subgraph app["アプリ層"]
    API["FastAPI 8000"]
  end
  subgraph async["非同期処理"]
    R["Redis"]
    W["Celery Worker GPU"]
  end
  subgraph data["データ"]
    DB[("SQLite")]
    DL["downloads"]
    UP["user_prompts"]
  end
  subgraph llm["LLM"]
    OL["Ollama"]
  end

  FE -->|api| NG
  NG -->|proxy| API
  API --> DB
  API --> DL
  API --> UP
  API -->|tags 一覧| OL
  API -->|send_task| R
  R --> W
  W --> DB
  W --> DL
  W --> UP
  W --> OL
```

- **フロントエンド**: Vite + React + TypeScript。本番では Nginx が静的ファイルを配信し、`/api/*` を FastAPI にリバースプロキシする。
- **API サーバ**: FastAPI。DB 読み書き、ファイル受け取り、Celery タスク投入のみ。**Torch / Whisper を import しない**（軽量イメージ化のため）。エンドポイントは **`backend/main.py`** でアプリを組み立て、**`backend/routes/`**（`meta` / `auth` / `admin` / `profile` / `presets` / `jobs` / `records`）に分割。共通処理は **`backend/ollama_client.py`**（Ollama の URL・**`/api/tags`**・**`try_ollama_unload_model`**）、**`backend/presets_io.py`**、**`backend/http_utils.py`** 等に集約。**Ollama 推論の `POST /api/generate`** は **API コンテナでは呼ばず**、**ワーカー `tasks.call_llm`** が **`requests`** で **`ollama_generate_url()`** へ送る。
- **ワーカー**: 従来どおり `tasks.py`（`celery_app` にタスクを登録）。Redis 経由でジョブを受け取る。
- **共有ボリューム**: `data/`（DB・ユーザープロンプト一時）、`downloads/`（アップロード媒体）。

---

## 3. 物理構成（Docker Compose）

| サービス | イメージ / ビルド | 役割 |
|----------|-------------------|------|
| `frontend` | `frontend/Dockerfile` | Nginx + `frontend/dist`、`:8085` で公開 |
| `api` | `Dockerfile.api` | FastAPI（`requirements-api.txt` のみ）。**`llm-net` 参加**・**`OLLAMA_BASE_URL`** で Ollama の **`/api/tags`** を呼び UI 用モデル一覧を返す。**`MM_OPENAI_ENABLED`** を API・ワーカーに注入 |
| `worker` | 既存 `Dockerfile` | GPU・Whisper・MoviePy・LLM 呼び出し |
| `redis` | `redis:alpine` | Celery ブローカー |
| （外部）Ollama | 運用側で別起動（例: `llm-net` 上） | ローカル LLM。Compose には含めず、ワーカーは `llm-net` ＋ `OLLAMA_BASE_URL` で接続 |

API とフロントは同一 Docker ネットワーク上で、`frontend` の Nginx が `http://api:8000` にプロキシする。

---

## 4. Celery の分離方針

### 4.1 問題

`tasks.py` は `faster_whisper` / `torch` 等を import するため、API から `from tasks import process_video_task` すると API コンテナも重い依存が必要になる。

### 4.2 解決

- **`celery_app.py`**: `Celery` インスタンスのみ定義（軽量）。
- **`tasks.py`**: `from celery_app import celery_app` の上で **`@celery_app.task`** を定義。ワーカー起動時のみ読み込まれる重い import はこのファイルに残す。
- **API**: `from celery_app import celery_app` のみ import し、`celery_app.send_task("tasks.process_video_task", args=[...])` で投入。

タスク名はモジュール名 `tasks` と関数名から `tasks.process_video_task` となる（ワーカーが `-A tasks` で起動している前提）。

### 4.3 `backend/` パッケージ構成（ルーティングと共通処理）

| モジュール | 役割 |
|------------|------|
| `main.py` | `FastAPI` 生成、**CORS**、**lifespan**、`routes` 各 `APIRouter` の `include_router` |
| `routes/meta.py` | `/api/health`, `/api/version`, `/api/ollama/models` |
| `routes/auth.py` | `/api/auth/*` |
| `routes/admin.py` | `/api/admin/*` |
| `routes/profile.py` | `/api/me/llm` |
| `routes/presets.py` | `/api/presets`（中身は `presets_io`） |
| `routes/jobs.py` | `POST /api/tasks` |
| `routes/records.py` | `/api/records`, `/api/queue`, 破棄・エクスポート・summary |
| `ollama_client.py` | **`OLLAMA_BASE_URL`** 解決、**`GET /api/tags`**（モデル名一覧）、**`/api/generate` の URL**（**`ollama_generate_url`**）、**`try_ollama_unload_model`**（**`keep_alive: 0`** の POST。VRAM 解放。**api** はタグ取得のみ、**tasks** が URL とアンロードを利用） |
| `presets_io.py` | `presets_builtin.json`（**api**・**tasks.py**・**Streamlit `app.py`** で共有） |
| `storage.py` | ユーザープロンプト・**参考資料（Teams/メモ）**一時ファイル（**`merge_task_prompt_paths`**。**api** の multipart と **Streamlit**） |
| `http_utils.py` | エクスポート用ヘッダ、SQLite 行の dict 化 |
| `passwords.py` | bcrypt パスワード検証（ログイン） |

---

## 5. REST API 仕様（概要）

ベースパス: `/api`（本番は同一オリジンで `/api/...`、開発時は Vite が `localhost:8000` へプロキシ可）

| メソッド | パス | 説明 |
|----------|------|------|
| GET | `/api/health` | ヘルスチェック |
| GET | `/api/ollama/models` | **`{ "models": string[] }`**。サーバが **`OLLAMA_BASE_URL`** の Ollama **`GET /api/tags`** を呼び、各エントリの **`name`**（なければ **`model`**）をソートして返す。ブラウザは Ollama に直アクセスしない。**認証不要**（モデル名は機密ではなく、未ログイン時も候補取得可能） |
| GET | `/api/version` | `version.py` の版情報 |
| GET | `/api/presets` | `presets_builtin.json` の内容（**`backend/presets_io.load_presets_dict`**、`routes/presets.py`） |
| POST | `/api/tasks` | `multipart/form-data`: `metadata`（JSON 文字列）、`file`（必須）、任意で `prompt_extract` / `prompt_merge`（.txt）、**`supplementary_teams` / `supplementary_notes`**（`.txt` / `.md` / `.vtt`。議事録の参考資料。保存は **`backend.storage.merge_task_prompt_paths`**） |
| GET | `/api/records` | クエリ: `days`, `search`, `category`, `status_filter`、任意で **`limit`**（1〜500、省略時は当該フィルタの**全件**）、**`offset`**（0 以上、既定 0）。**JSON 本文**: **`{ "items": RecordRow[], "total": number }`** — `items` はページング後の一覧、`total` は同一フィルタに一致する**総件数**（UI のページ数計算用）。**`backend/schemas.py`** の **`RecordsPageResponse`**。Streamlit `app.py` は従来どおり **`limit` 省略**で全件取得 |
| GET | `/api/queue` | 待機・処理中レコード一覧。**`MM_AUTH_SECRET` 有効時は全登録ユーザー分をマージ**（`database.get_active_queue_records_global`）。各要素に **`job_owner`**（ログイン ID）・**`is_mine`**（閲覧者が投入者か）。破棄・エクスポートは従来どおり **自分のジョブのみ**可 |
| GET | `/api/records/{task_id}` | 1 件取得（ポーリング用） |
| PATCH | `/api/records/{task_id}/summary` | 議事録本文の手動上書き `{ "summary": "..." }` |
| GET | `/api/records/{task_id}/export/minutes` | 議事録を `text/markdown` でダウンロード（長大本文用・data URL 回避） |
| GET | `/api/records/{task_id}/export/transcript` | 書き起こし全文を `text/plain` でダウンロード |
| GET | `/api/auth/status` | **`AuthStatusResponse`**（`backend/schemas.py`）。`auth_required`, `bootstrap_needed`, `self_register_allowed`、`email_notify_feature_enabled`（`MM_EMAIL_NOTIFY_ENABLED`）、`email_notify_available`（上記 ON かつ SMTP 設定済み）、`openai_enabled`（`MM_OPENAI_ENABLED`）、**`error_report_available`**（SMTP・管理者宛先等により「不具合・エラーの報告」が使えるか）、**`minutes_retention_days`**（**`database.minutes_retention_days()`** の整数。議事録レコードの保存日数。React は「議事録アーカイブ」見出し直下の説明に使用）。`MM_AUTH_SECRET` 未設定時は `auth_required: false` ・他はフラグのみ意味を持つ |
| POST | `/api/auth/bootstrap` | 初回のみ（registry のユーザー数が 0）。`{ email, password }` で最初の **管理者** を作成し JWT を返す |
| POST | `/api/auth/register` | ユーザーが 1 人以上いるとき、自己登録で **一般ユーザー** を追加し JWT を返す（`MM_AUTH_SELF_REGISTER=0` で無効） |
| POST | `/api/auth/login` | `{ email, password }` → JWT |
| GET | `/api/auth/me` | Bearer 必須（認証オフ時は `email: ""`）。`{ email, is_admin }` |
| GET | `/api/admin/users` | **管理者のみ**。ユーザー一覧（パスワードは含まない） |
| POST | `/api/admin/users` | **管理者のみ**。`{ email, password, is_admin }` でユーザ追加 |
| PATCH | `/api/admin/users/{login_email}/password` | **管理者のみ**。`{ new_password }`（`login_email` は URL エンコード） |
| PATCH | `/api/admin/users/{login_email}/role` | **管理者のみ**。`{ is_admin }`（最後の管理者の降格は不可） |
| DELETE | `/api/admin/users/{login_email}` | **管理者のみ**。自分自身・最後の管理者は不可 |
| GET | `/api/admin/usage/summary` | **管理者のみ**。クエリ **`days`**（1〜**365**、既定 30）。**`AdminUsageSummaryResponse`**（`backend/schemas.py`）。投入件数・書き起こしのみ／議事録生成の割合・Ollama/OpenAI 件数・**Ollama/OpenAI それぞれの議事録生成ジョブに対するモデル別内訳**・動画・音声向け Whisper プリセット集計・媒体種別（拡張子から推定）に加え、**`metrics_rollup`**（**`UsageMetricsRollup`**）。完了ジョブでメトリクスが記録された件向けの合計・平均（入力バイト、媒体の長さ、音声抽出・Whisper・議事録 LLM の壁時計、書き起こし文字数、LLM チャンク数など。詳細は **§5.2**）。**認証シークレット未設定時は空集計** |
| GET | `/api/admin/usage/events` | **管理者のみ**。クエリ **`days`**（1〜365）、**`limit`**（1〜500）、**`offset`**。直近の投入ログ行一覧と総件数（ページング用）。各行は **§5.2** のメトリクス列（`input_bytes` 等）を含む（未記録は `null`） |
| GET | `/api/admin/usage/settings-summary` | **管理者のみ**。クエリ **`days`**（1〜365）。通知方式（`notification_type`）・参考資料添付率（Teams/メモ/いずれか）・防御イベント件数（`rate_limited` / `upload_too_large` / `disk_low`）を返す。 |
| GET | `/api/admin/usage/notes` | **管理者のみ**。運用メモ一覧（新しい順） |
| POST | `/api/admin/usage/notes` | **管理者のみ**。`{ "body": "…" }`（1〜8000 文字）でメモ追加。著者は JWT の `sub`（ログイン ID） |
| DELETE | `/api/admin/usage/notes/{note_id}` | **管理者のみ**。メモ削除 |
| GET | `/api/me/llm` | ログインユーザーの OpenAI 設定の参照。**`{ openai_configured, openai_model, openai_feature_enabled }`**。認証オフ時はキー未設定扱い・モデルは既定文字列。**`MM_OPENAI_ENABLED` オフ**時は `openai_feature_enabled: false`（`openai_configured` も偽として扱う） |
| PATCH | `/api/me/llm` | **`{ openai_api_key?, openai_model? }`**。registry に保存。**`MM_OPENAI_ENABLED` オフ**のときは 400。認証オフ時は 400（サーバ保存不可） |
| POST | `/api/records/{task_id}/discard` | 待機・実行中ジョブの破棄。DB を cancelled、**Celery `revoke`（terminate）**、投入ファイル・ユーザープロンプト一時を削除 |

### 5.1 `POST /api/tasks` の `metadata`（JSON）

`backend/schemas.py` の `TaskSubmitMetadata` に対応。

- 通知: `notification_type` … `browser` | `webhook` | **`email`** | `none`（**Webhook** 時は **`email`** フィールド必須。**メール** 時は SMTP 未設定なら 503。認証かつ宛先空ならログイン ID のメールへ送る等、実装参照）
- LLM: `llm_provider` … `ollama` | `openai`
  - **`MM_OPENAI_ENABLED` オフ**のとき **`openai` は 400**（Ollama のみ）
  - **認証有効**かつ OpenAI: フォームのキーではなく **registry に保存された API キー**を使用。未保存なら 400
  - **認証オフ**かつ OpenAI: 従来どおりリクエストの **`openai_api_key`** 必須
- Ollama: **`ollama_model`** 文字列（ワーカーが `OLLAMA_BASE_URL` へ接続するときのモデル名）
- **Ollama 推論オプション（クライアント `metadata` では指定しない）**: ワーカー **`tasks.call_llm`**（Ollama 経路）が **`requests.post(ollama_generate_url(), …)`** で送る。**`options.num_ctx` は既定 4096**（**`backend/ollama_model_profiles.py`**。VRAM・KV 負荷抑制。**`8192` から引き下げた経緯**は **`document/design_spec.md` §3.1.2**）。**`MM_OLLAMA_PROFILES_PATH`** の JSON でモデル別上書き可。**`timeout=600`** 秒。
- **Ollama VRAM 早期解放**: **`backend/ollama_client.try_ollama_unload_model`** を **`tasks._try_ollama_unload_for_config`** から呼ぶ。対象は **`_cleanup_after_cancel`**（処理途中の破棄）、**`fail()` によるエラー完了**、**`process_video_task` 外側の `except`**（いずれも OpenAI 経路ではスキップ）。**`process_video_task` 先頭で既に cancelled の早期 return**（Ollama 未使用想定）は **アンロードしない**。環境変数 **`OLLAMA_UNLOAD_ON_TASK_END`** が **`0` / `false` / `no`** のときはアンロード要求を送らない（既定はオン扱い）。
- **マージ LLM 失敗時**: **`call_llm` が例外**（タイムアウト等）のとき **`Merge failed (Error: …)` と抽出 JSON** を連結した文字列を **`summary`** にし、**`status` は `completed`** のまま（**このフォールバック経路では `try_ollama_unload` は呼ばない**。実装上の注意として、タイムアウト後も Ollama 側がモデル保持し続ける場合は **`OLLAMA_UNLOAD_ON_TASK_END`** 運用やサーバ側設定の検討対象）。
- 会議メタ: `topic`, `meeting_date`, `category`, `tags`, `preset_id`
- 精度用: `context` … `purpose`, `participants`, `glossary`, `tone`, `action_rules`
- **書き起こしのみ**: `transcript_only` … 真のとき動画・音声は Whisper（または .txt/.srt 直読み）までとし、**議事録用 LLM（抽出・マージ）は実行しない**（Ollama/OpenAI は使わない）
- **音声認識の品質（Whisper）**: `whisper_preset` … `fast` | `balanced` | `accurate`（**動画・音声**の faster-whisper 探索の強さ。ワーカー **`tasks.py`** で解釈。既定 **`accurate`（高精度）**）

アップロードファイルは衝突回避のため API 側で `downloads/{task_id}_{元ファイル名}` に保存し、DB の `filename` には元の表示名を保存する。

### 5.2 利用状況ログ（registry・管理者向け）

- **記録タイミング**: **`MM_AUTH_SECRET` が設定されている**（`_auth_secret_configured()` が真）とき、**ジョブ受付直後**（`save_initial_task` のあと）。**React**: **`POST /api/tasks`** で記録（ユーザー未特定時は `user_email` が空）。**Streamlit `app.py`**: **`auth_enabled()`** のとき（フォームのメール等でユーザー列を埋める。未入力なら空）。
- **保存先**: **`data/registry.db`** の **`usage_job_log`**（`task_id` UNIQUE）および **`usage_guard_events`**。マイグレーションは **`database._migrate_usage_tables`**（`init_registry_db` から。メトリクス列は **`database._migrate_usage_job_metrics_columns`**、設定利用列は **`database._migrate_usage_job_feature_columns`**）。
- **保存するフィールドの要点（メタ）**: `user_email`（正規化）、`transcript_only`、`llm_provider`（`ollama` / `openai`）、`model_name`（当該ジョブで選ばれたモデル）、`whisper_preset`、`notification_type`、`has_supplementary_teams`、`has_supplementary_notes`、`media_kind`（**元ファイル名は保存せず**、`database.usage_media_kind_from_filename` が **basename の拡張子のみ**から `video` / `audio` / `srt` / `txt` / `other` を決定）。**議事録本文・書き起こし全文は含めない**。
- **メトリクス（負荷・稟議・容量の目安）**: 受付時に **`input_bytes`**（アップロード本体のバイト数。API は読み込んだ `body` の長さ、Streamlit は保存後ファイルのサイズ）。ジョブ**完了時**にワーカー（**`tasks.process_video_task`** 等）が **`database.update_usage_job_metrics`** で更新する列として、**`media_duration_sec`**（動画・音声の再生相当の長さ。セグメント等から推定）、**`audio_extract_wall_sec`**（動画から音声抽出の壁時計）、**`whisper_wall_sec`**（faster-whisper 文字起こしの壁時計）、**`transcript_chars`**（書き起こしの文字数）、**`extract_llm_sec`** / **`merge_llm_sec`**（議事録用 LLM のチャンク抽出・マージの壁時計）、**`llm_chunks`**（抽出 LLM のチャンク数）がある。**`.txt`/`.srt` 直投入**など経路によっては一部が 0 または未使用。
- **管理者 API の集計**: **`GET /api/admin/usage/summary`** の **`metrics_rollup`** は、期間内の行のうち **`transcript_chars IS NOT NULL`**（完了時にメトリクスが書き込まれたジョブ）のみを対象に合計・平均を算出する。**未完了・失敗でメトリクス未更新のジョブは含まれない**。
- **防御イベント**: レート制限（429）、容量不足（503）、サイズ超過（413）を `usage_guard_events` に `event_type` と匿名化した `actor_key_hash` で記録し、**`GET /api/admin/usage/settings-summary`** で件数確認できる。
- **運用メモ**: 同じ registry の **`usage_admin_notes`**。**`database.usage_admin_note_*`** と上記 **`/api/admin/usage/notes`**。
- **集計の上限**: サマリ・イベントとも **クエリ `days` は最大 365**（サーバ側でも `database.admin_usage_*` でクランプ）。

---

## 6. フロントエンド設計

- **技術**: React 18、Vite 5、TypeScript、`react-markdown`（JSON でない要約の表示用）。
- **状態**: フォームはローカル state。ブラウザ通知用に `localStorage` キー `mm_pending_tasks` で `task_id` 一覧を保持し、10 秒間隔で `GET /api/records/{id}` をポーリング。**通知を使うユーザーは、ブラウザが出す通知許可のポップアップ／バナーで「許可」する**（ブロックのままでは通知が届かない）。
- **ジョブ破棄**: キュー表示などから **`POST /api/records/{task_id}/discard`**（`discardRecord`）を呼び、待機・処理中タスクを取消・ファイル掃除。
- **認証 UI**: `GET /api/auth/status` で `bootstrap_needed` が真のとき **初回セットアップ**（管理者・パスワード確認）→ `POST /api/auth/bootstrap`。それ以外は **ログイン** / **新規登録**タブ（`self_register_allowed` が真のとき）→ `POST /api/auth/login` または `POST /api/auth/register`。JWT は `localStorage`（`mm_auth_token`）。API 呼び出しは `Authorization: Bearer`。
- **右上アカウントメニュー**: ユーザーアイコンを押すとドロップダウンを表示。**メイン画面**では「設定」「サインイン／サインアウト」。認証かつ管理者のときは追加で「ユーザー・権限管理」「**利用ログ画面**」（管理者専用のログ画面を開く）。**初回セットアップ／ログイン画面**では「説明・設定」「フォームへ」（スクロール／フォーカス）。
- **設定ドロワー（右スライド）**: 「設定」で開く。認証時は **一般**タブにアカウント表示・**OpenAI（サーバ保存キー・モデル、`GET/PATCH /api/me/llm`）** 等。**`openai_enabled`（auth/status）が偽**のときは OpenAI 登録 UI を出さない（環境で `MM_OPENAI_ENABLED` オフ）。`is_admin` のときのみタブ **ユーザー・権限** を表示する。利用ログは右上メニューの **利用ログ画面** で期間選択（7 / 30 / 90 / **365 日**）、サマリ・**メトリクス集計（負荷・容量の目安）**・イベント一覧（行ごとのメトリクス列）・運用メモ（**`frontend/src/api.ts`** の **`adminUsageSummary`** / **`adminUsageEvents`** 等。**`AdminUsageSummary.metrics_rollup`**）を確認する。**ヘルプ**（`HelpPage`）に利用ログの範囲とプライバシーを記載。一般タブのアカウント欄に「管理者」と表示される場合がある。
- **ヘルプ**: **`HelpPage`**（`#help`）。メインタイトル横の「ヘルプ」ボタン・アカウントメニューから開く。使い方・Whisper 品質・通知などを集約。
- **議事録アーカイブ**: 見出しの**直下**（横並びではなくブロック下）に、**`minutes_retention_days`** に基づく**保存期間・自動削除**の説明文を表示（例: 既定 90 は UI 上「約3か月（90日）」）。文言はサーバ返却値と一致させる。**一覧は 1 ページあたり最大 10 件**（`ARCHIVE_PAGE_SIZE`）。**総件数が 10 を超える**とき、一覧下に **前へ／次へ** と **現在ページ／総ページ・全件数** を表示し、**`GET /api/records?limit=10&offset=…`** で取得する。キーワード・分類・ステータスを変えたときは **1 ページ目に戻す**。削除等で現在ページが空になった場合は **最終ページへ寄せて再取得**する。
- **音声認識の品質（Whisper）**: 左パネル「解析設定」で **`whisper_preset`** を **`<select>`** で選択。ラベル・ツールチップはユーザー向け日本語（所要時間・精度のトレードオフを説明）。
- **フォームの `<select>`**: 左サイドバー・設定ドロワー等のプルダウンは、**`onChange` 後に `blur()`** してフォーカスリングが残りにくいようにしている。
- **Ollama モデル欄**: **初回マウント時と認証状態更新時（`authNonce`）のみ** **`GET /api/ollama/models`** で候補を取得（**ページ再読み込みで更新**。ウィンドウフォーカスや定期ポーリングは行わない）。**ネイティブ `<select>`** で候補のみ選択（手入力不可）。現在値が一覧に無い場合は先頭候補へ寄せる。
- **OpenAI オフ時の投入フォーム**: 「AI の接続先」は Ollama のみ表示。**OpenAI 用モデルプルダウンは表示しない**（未使用のため）。
- **マージのみ失敗した場合の議事録表示**: **`status` は `completed`**。本文は先頭が **`Merge failed (Error: …)`** で続けて **マージ前の抽出 JSON**（`react-markdown` で JSON として表示されない場合あり。生テキストとして閲覧）。
- **環境変数**: `VITE_API_BASE`（空なら相対パス `/api` — 本番 Nginx 配下で利用）。**秘密をここに入れないこと**（後述 §7.2）。

---

## 7. セキュリティ・運用上の注意

- **API キー**: フロントから OpenAI キーを送る設計のため、**HTTPS 必須**の本番運用を推奨。社内 VPN 内のみの利用を前提とする。
- **認証**: `MM_AUTH_SECRET`（十分に長いランダム文字列）を設定すると JWT 認証が有効。初回は **ユーザー 0 件のときだけ** `POST /api/auth/bootstrap` または `MM_BOOTSTRAP_ADMIN_USER` / `MM_BOOTSTRAP_ADMIN_PASSWORD` で最初の管理者を作成可能。外向き公開する場合は HTTPS・IP 制限・WAF 等と併用すること。
- **CORS**: `CORS_ORIGINS` 環境変数（カンマ区切り）。開発時は `http://localhost:5173` を含める。LAN の IP でフロントにアクセスする場合は当該オリジンも列挙する。
- **アップロード上限**: Nginx `client_max_body_size 5g`。
- **運用推奨**: 2GB を超える素材は事前分割して投入する。
- **API 防御**: `POST /api/tasks` でファイル本体をチャンク保存しつつ上限（既定 5GiB）を検証。一定時間あたりの投入件数を制限（429）し、`downloads` の空き容量が閾値未満の場合は受付を拒否（503）。

### 7.1 設定・秘密情報の「どこに書くか」（コード所在）

| 種別 | 主な読み取り元 | 備考 |
|------|----------------|------|
| JWT 署名鍵・トークン TTL・自己登録可否 | `backend/auth_settings.py`（`MM_AUTH_SECRET`, `MM_AUTH_TOKEN_HOURS`, `MM_AUTH_SELF_REGISTER`） | **秘密鍵はこのモジュール経由でサーバ内のみ**。クライアント JS に含めない。 |
| registry を使うか（認証の前提） | `database.py` の `_auth_secret_configured()`（`MM_AUTH_SECRET` の有無） | 上記と同じ環境変数を参照。 |
| CORS 許可オリジン | `backend/main.py`（環境変数 `CORS_ORIGINS`。Compose では `MM_CORS_ORIGINS` から注入） | **ルートハンドラ**は `backend/routes/`。秘密ではないが、**許可先を広げすぎない**こと。 |
| 議事録の保存期限・自動削除 | **`database.py`**: **`DEFAULT_MINUTES_RETENTION_DAYS`（90）**、**`minutes_retention_days()`**（環境変数 **`MM_MINUTES_RETENTION_DAYS`** を解釈。**未設定・非数時は 90**。**値が 183 のときは 90 日として扱う** — 旧既定からの移行）、**`purge_expired_minutes_db_path`** 等 | **`created_at` が N 日より古い**レコードを対象に、**`pending` と `processing%` を除き**関連ファイル掃除のうえ **DELETE**。**N≤0** で自動削除オフ。起動時 **`main.py` lifespan**、一覧取得前・タスク完了後などで実行。Compose は api/worker に **`MM_MINUTES_RETENTION_DAYS`**（既定注入 **`:-90`**）を渡す。フロントは **`/api/auth/status` の `minutes_retention_days`** で文言と一致させる |
| 議事録一覧（フィルタ・件数・ページング） | **`database.py`**: **`_recent_records_where_clause`**、**`count_recent_records`**、**`get_recent_records(..., limit=None, offset=0)`**（`limit` 省略時は **LIMIT なし**で全件。指定時は **`ORDER BY created_at DESC` + `LIMIT`/`OFFSET`**） | **`routes/records.py`** の **`GET /api/records`** が `count_recent_records` と `get_recent_records` を呼び **`RecordsPageResponse`** を返す。React の **`listRecords`**（`frontend/src/api.ts`）が `limit`/`offset` を付与可能 |
| OpenAI 連携の ON/OFF | **`feature_flags.py`**（**`MM_OPENAI_ENABLED`**。`0` / `false` / `no` / `off` / 空でオフ。未設定時はオン＝後方互換） | API・ワーカー・Streamlit で共通。オフ時は `PATCH /api/me/llm` 不可・`POST /api/tasks` で `openai` 不可。 |
| API から Ollama への接続（タグ一覧・URL 解決） | **`backend/ollama_client.py`**（**`OLLAMA_BASE_URL`**、未設定時は `http://127.0.0.1:11434`）。呼び出しは **`routes/meta.py`** | Docker では **api を `llm-net` に参加**させ、ワーカーと同じ Ollama ホストを指定。**ワーカー**の推論 URL も同一モジュールで整合。 |
| Ollama 推論（`num_ctx`・タイムアウト） | **`tasks.call_llm`** → **`requests.post`**（**`backend/ollama_client.ollama_generate_url()`**）。**`num_ctx: 4096`**・**`timeout=600`**（コード固定。環境変数では切り替えない） | UI・`metadata` からは変更不可。**`pipeline/02_extract.py`**・**`03_merge.py`** は各自 **`OLLAMA_URL`**／**`NUM_CTX=4096`**・**`REQ_TIMEOUT=600`**（ワーカーと同趣旨）。**VRAM アンロード**: **`try_ollama_unload_model`**・**`OLLAMA_UNLOAD_ON_TASK_END`**（§5.1） |
| プリセット JSON の単一ソース | **`backend/presets_io.py`** | **`routes/presets.py`**・**`tasks.py`**・**Streamlit `app.py`** が同じ読み込み経路を使う。 |
| エクスポートヘッダ・行 dict 化 | **`backend/http_utils.py`** | **`routes/records.py`** で利用。 |
| ログイン時パスワード検証 | **`backend/passwords.py`** | **`routes/auth.py`** で利用。 |
| ユーザープロンプト・参考資料一時保存 | **`backend/storage.py`**（**`merge_task_prompt_paths`**） | **`routes/jobs.py`**（multipart）と **Streamlit `app.py`**（**`save_uploaded_prompts`** 経由）が同じ保存規約を使う。 |
| 管理者向け利用ログ・集計 | **`database.py`**: **`record_usage_job_submission`**（**`input_bytes`** / `notification_type` / 参考資料有無 含む）、**`record_usage_guard_event`**、**`update_usage_job_metrics`**、**`admin_usage_summary`**（**`metrics_rollup`**）、**`admin_usage_events`**、**`admin_usage_settings_summary`**、**`usage_admin_notes_*`**（テーブル **`usage_job_log`**, **`usage_guard_events`**, **`usage_admin_notes`**）。**`routes/jobs.py`**（受付時に記録）、**`app.py`**（**`auth_enabled()`** 時）、**`tasks.py`**（完了時メトリクス更新）。**`routes/admin.py`**（**`/api/admin/usage/*`**） | **議事録・書き起こし本文は保存しない**。集計期間 **最大 365 日**。メトリクス集計は **`transcript_chars` 記録済み行**に限定（**§5.2**）。 |
| メール通知（SMTP） | API・ワーカー双方に **`MM_SMTP_*`**（`MM_SMTP_HOST`, `MM_SMTP_FROM` 必須など。詳細は `.env.example`） | `email_notify_available` の判定とタスク検証に使用。 |
| ホスト公開ポート・ブローカ URL | `docker-compose.yml`、`.env` / `.env.example` | **ポート番号自体は「秘密」ではない**が、不要なポートを外向きに開かない運用とセット。 |
| フロントの API ベース URL | ビルド時 `VITE_API_BASE` → `frontend/src/api.ts` の `PREFIX` | **公開してよい URL のみ**（後述）。 |

### 7.2 外部に出してはいけないもの（うっかり混入防止）

以下を **リポジトリのコミット・静的フロントのビルド成果物・公開スクリーンショット・サポート添付・ログ出力** に含めないこと。

| 対象 | 理由 | 典型の誤り |
|------|------|------------|
| `MM_AUTH_SECRET` | JWT 偽造・セッション乗っ取りに直結 | `.env` を git add する、`docker-compose.yml` のまま本番運用する、環境変数一覧をそのまま貼る |
| `MM_BOOTSTRAP_ADMIN_PASSWORD` | 初期管理者の乗っ取り | 同上 |
| ユーザーパスワード（平文） | そもそもサーバにも平文保存しない（bcrypt のみ） | ログにリクエストボディを出す |
| 利用者の OpenAI API キー（registry 保存分） | 第三者による課金・モデル悪用 | DB ファイルを無暗に配布、バックアップを公開領域に置く |
| Celery `WEBHOOK_URL` に含まれるシークレット付き URL | Webhook への不正 POST | 設定値をログに全文出力 |

**JWT（`localStorage` の `mm_auth_token`）**は署名鍵ではなく**トークン本体**である。秘密鍵と混同しないこと。XSS や共有端末では漏えいリスクがあるため、本番は HTTPS・CSP 等の一般的対策と併用する。

### 7.3 フロントエンド（Vite）で「出てよい情報」だけ

- `VITE_*` は **ビルド時にクライアント用 JS に埋め込まれる**と考える。API の**公開ベース URL**（例: 同一オリジンなら空）程度に留める。
- **`VITE_*` に `MM_AUTH_SECRET`・API キー・パスワードを渡さない。** 認証はログイン後の Bearer のみ。
- 開発時の `VITE_DEV_API_PROXY`（`vite.config.ts`）は開発サーバ用であり、本番 Nginx 配信の `dist` には乗らないが、**チーム共有の `.env` に本番秘密を書かない**運用にすること。

### 7.4 運用チェックリスト（リリース・公開前）

1. **本番**では `MM_AUTH_SECRET` を `openssl rand -hex 32` 等で**一意の長い値**にし、リポジトリ既定のフォールバック（Compose 未指定時の文字列）を使わない。
2. `.env` を **`.gitignore` 対象のまま**にし、秘密をコミットしない。
3. `MM_CORS_ORIGINS` は**実際に使うフロントのオリジンのみ**（ワイルドカードや過剰な `*` を避ける）。
4. API コンテナの **8000 をインターネットに直晒さない**（フロント Nginx 経由、またはリバースプロキシ後段のみ）。
5. スクリーンショット・インシデント報告に **Compose 画面・環境変数一覧・registry.db** が写り込まないよう注意する。

---

## 8. レガシー（Streamlit）

- **`app.py`**: エントリのみ。`st.set_page_config`・`db.init_db`・サイドバー／メインのレイアウト呼び出しに留める。ローカルでは `streamlit run app.py` や従来どおり全量 `Dockerfile` で起動可能。
- **`streamlit_app/`**（`app.py` 専用の薄い UI 層）:
  | モジュール | 役割 |
  |------------|------|
  | `constants.py` | ロゴ SVG パス等 |
  | `styles.py` | `inject_ui_styles`（グローバル CSS） |
  | `render.py` | `render_minutes`・`render_error_hints`・`save_uploaded_prompts`（`backend.storage` への委譲） |
  | `task_status.py` | DB ステータス文字列 → 進捗バー用 `(percent, caption)` |
- **Python との共通化**: プリセット選択肢は **`backend.presets_io.preset_options_for_ui`**。カスタムプロンプト・参考資料は **`streamlit_app.render.save_uploaded_prompts`** → **`backend.storage.merge_task_prompt_paths`**（バイト列）で **React の `POST /api/tasks`** と同じ `data/user_prompts/{task_id}/` レイアウトに揃える。
- **推奨**: 本番・新規運用は **React + FastAPI + Compose（frontend / api / worker）** を標準とする。

---

## 9. 開発フロー（ローカル）

1. Redis を起動（または Compose で redis のみ）。
2. プロジェクトルートで `pip install -r requirements-api.txt` → `uvicorn backend.main:app --reload --port 8000`。
3. `frontend/` で `npm install` → `npm run dev`（`/api` を Vite が 8000 にプロキシ）。
4. 別ターミナルで GPU ワーカー: `celery -A tasks worker --loglevel=info`（従来どおり `requirements.txt` フルセット）。

---

## 10. 今後の拡張候補

- OpenAPI クライアント生成（TypeScript）で型を API と完全同期
- OAuth2 / SSO 連携
- タスク結果の WebSocket / SSE プッシュ（ポーリング廃止）
- API とワーカーでオブジェクトストレージ経由のファイル受け渡し

---

## 11. 環境依存値の扱い（ハードコード方針）

- **秘密情報の所在と流出防止**は **§7.1〜7.4** を正とする（本節はホスト名・URL のハードコード方針に限定する）。
- **コンテナ内のホスト名**（例: Compose の `redis`、`api`）は Docker のサービス名であり、**特定サーバーの固有名ではない**。Nginx の `proxy_pass http://api:8000` も同様。
- **ホストマシンや社内 DNS に依存する値**は可能な限り **環境変数**に寄せる:
  - API: `CORS_ORIGINS`（Compose では `MM_CORS_ORIGINS` から渡す）
  - API・ワーカー: **`OLLAMA_BASE_URL`**（API は **`/api/tags`**、ワーカーは推論）、**`MM_OPENAI_ENABLED`**
  - ワーカー: `CELERY_BROKER_URL`、`WEBHOOK_URL`
  - メール通知: **`MM_SMTP_*`**（API とワーカーで同一値を推奨）
  - 議事録 DB 保持: **`MM_MINUTES_RETENTION_DAYS`**（**`database.minutes_retention_days()`**。未設定時は **90**。**183 は 90 として扱う**。**0 以下**で自動削除オフ。Compose 既定 **`:-90`**）
  - ワーカー（参考資料）: **`MM_SUPPLEMENTARY_MAX_CHARS`** … Teams/メモ参考テキスト結合後の**文字数上限**（既定 **120000**）。**ワーカー import 時**に読み取り。**変更後は worker 再起動**。**根拠・トレードオフ**は **`document/design_spec.md` §3.1.2**
  - ワーカー（Ollama オプション上書き）: **`MM_OLLAMA_PROFILES_PATH`** … JSON で **`num_ctx` 等**をモデル名プレフィックス別に指定（**`config/ollama_profiles.example.json`**）。**`MM_OLLAMA_PROFILES=0`** で無効
  - CLI パイプライン: `OLLAMA_BASE_URL`、`OLLAMA_MODEL`（**`pipeline/02_extract.py`**・**`03_merge.py`**）。いずれも **`_ollama_generate_url()`** で URL を組み立て、**`num_ctx` 4096**・**タイムアウト 600 秒**（**`02_extract`** はペイロード内、**`03_merge`** は **`NUM_CTX` / `REQ_TIMEOUT`**）。**`extract_json_block`** は **`02_extract` 内の関数**（**`tasks.py` に同名関数**があり、ワーカーはそちらを使用。共通化モジュールはない）
  - ローカル開発: リポジトリ直下 `.env` の `VITE_DEV_API_PROXY`（Vite が `/api` を転送する先）
- **既定値**（例: API の CORS に `localhost:5173`、Celery の `redis://localhost:6379/0`、ワーカーの `OLLAMA_BASE_URL=http://ollama-server:11434`）は **開発・Compose 向けのデフォルト**（`llm-net` 上の Ollama コンテナ名想定）であり、本番では `.env` やオーケストレーション側で上書きすること。

### 11.1 GT-2222 HTTPS サブパス公開の確定運用

- 公開 URL は **`https://gt-2222/meetingminutesnotebook/`**（末尾スラッシュ推奨）。
- Compose の `.env`（`docker-compose.yml` と同階層）に以下を必ず設定する。
  - `VITE_BASE_PATH=/meetingminutesnotebook/`
  - `VITE_API_BASE=/meetingminutesnotebook`
  - `MM_CORS_ORIGINS` に `https://gt-2222` を含める（パスは書かない）。
- フロント配信 Nginx（`frontend/nginx.conf`）はサブパスを受けた際にプレフィックスを剥がして `/index.html`・`/assets` に解決する（`/meetingminutesnotebook/index.html` を直接探しに行かない）。
- デプロイスクリプト運用では次を既定とする。
  - `scripts/server-rebuild.sh` / `.bat`: `frontend` を `--no-cache` ビルド
  - `scripts/tar-scp.sh`: `TAR_SCP_SET_ENV=gt2222` で `config/gt-2222.env` を `.env` として配置可能
- **TLS（社内ルートCA + サーバ証明書）**
  - ホストNginxの `ssl_certificate` / `ssl_certificate_key` には **`gt-2222.crt`（CA署名）** と **`gt-2222.key`** を指定する（`rootCA.crt` はクライアント配布用。Nginx のサーバ証明書には使わない）
  - 同一 `server { listen 443 ssl; server_name GT-2222; }` 内の **全 `location`（例: `/jupyter/`）** は同じ証明書を共有する（`location` 単位で証明書は変えられない）
  - サーバ側確認: `openssl s_client` で **issuer がルートCA**、**SAN に `DNS:gt-2222`** であること
  - Windowsクライアント: **`rootCA.crt` を「信頼されたルート証明機関」に手動ストア指定**でインポート（ウィザードの「自動選択ストア」だけだと未信頼のままになりやすい）
  - **ブラウザ通知**利用時: 通知許可のポップアップ／バナーが出たら **許可**する（ブロックのままでは完了通知が届かない）
- 詳細な試行錯誤・切り分け手順は `document/gt2222_https_subpath_troubleshooting.md` を参照。

### 11.2 要件IDトレーサビリティ（実装・テスト・品質報告）

本書で扱う主要要件は、`document/coverage_report_2026-04-10.md` の要件ID（RQ-01〜RQ-10）と対応させる。  
実装変更やテスト追加時は、下表とカバレッジ報告の両方を同時更新する。

| 要件ID | 要件概要 | 主実装 | 主テスト | 品質エビデンス |
| :--- | :--- | :--- | :--- | :--- |
| RQ-01 | ジョブ処理（正常/異常/キャンセル） | `tasks.py` | `tests/test_tasks_helpers.py` | `document/coverage_report_2026-04-10.md` |
| RQ-02 | 利用状況ログ（管理者） | `database.py`, `backend/routes/admin.py`, `backend/routes/jobs.py`, `app.py` | `tests/test_database_core.py`, `tests/test_admin_routes_core.py`, `tests/test_jobs_routes.py`, `tests/test_app_smoke.py` | 同上 |
| RQ-03 | 目安箱（投稿/管理） | `backend/routes/feedback.py`, `database.py`, `backend/schemas.py` | `tests/test_feedback_routes.py`, `tests/test_admin_suggestion_routes.py`, `tests/test_suggestion_box.py`, `tests/test_suggestion_schemas.py` | 同上 |
| RQ-04 | 認証/権限管理 | `backend/routes/auth.py`, `backend/auth_settings.py`, `backend/deps.py`, `database.py` | `tests/test_auth_routes.py`, `tests/test_runtime_misc.py`, `tests/test_backend_misc.py`, `tests/test_database_core.py` | 同上 |
| RQ-05 | タスク作成API | `backend/routes/jobs.py`, `backend/schemas.py` | `tests/test_jobs_routes.py` | 同上 |
| RQ-06 | 議事録一覧/詳細/更新 | `backend/routes/records.py`, `database.py` | `tests/test_records_routes.py`, `tests/test_database_core.py` | 同上 |
| RQ-07 | 通知（SMTP/Webhook） | `backend/smtp_notify.py`, `tasks.py`, `backend/routes/feedback.py` | `tests/test_smtp_notify.py`, `tests/test_tasks_helpers.py`, `tests/test_feedback_routes.py` | 同上 |
| RQ-08 | LLM/Ollama連携 | `backend/ollama_client.py`, `backend/ollama_model_profiles.py`, `tasks.py` | `tests/test_ollama_modules.py`, `tests/test_tasks_helpers.py` | 同上 |
| RQ-09 | 起動/ライフサイクル | `backend/main.py`, `feature_flags.py` | `tests/test_backend_main.py`, `tests/test_backend_misc.py` | 同上 |
| RQ-10 | Streamlit経路（レガシーUI） | `app.py`, `streamlit_app/` | `tests/test_app_smoke.py` | 同上 |

---

## 12. 変更履歴（ドキュメント）

| 版 | 日付 | 内容 |
|----|------|------|
| 1.0 | 2025-03-22 | 初版（FE/BE 分離、Compose、API 一覧、Celery 分離方針） |
| 1.1 | 2025-03-22 | 環境変数・ハードコード方針（§11） |
| 1.2 | 2026-03-23 | 初回セットアップ・ログイン・管理者 API／画面、`registry.users.is_admin`（§5・§6・§7） |
| 1.3 | 2026-03-23 | `POST /api/auth/register`・`self_register_allowed`・`MM_AUTH_SELF_REGISTER` |
| 1.4 | 2026-03-23 | 物理構成の `frontend/Dockerfile` 表記修正。§6 にアカウントドロップダウン・設定ドロワー「一般／ユーザー・権限」タブ・管理者権限 UI を反映 |
| 1.5 | 2026-03-23 | ログイン ID をメールに合わせ §5 認証 API を更新。**§7.1〜7.4** に設定・秘密のコード所在と外部流出防止を明記 |
| 1.6 | 2026-03-23 | **`GET /api/ollama/models`**、**`GET/PATCH /api/me/llm`**、**`POST .../discard`**。**`AuthStatus`** の **`email_notify_available` / `openai_enabled`**。**`MM_OPENAI_ENABLED`**・**`feature_flags.py`**・api の **`llm-net` / `OLLAMA_BASE_URL`**。§5.1 の通知・OpenAI 分岐。§6 の Ollama コンボボックス・OpenAI オフ UI。論理構成図に API→Ollama（tags） |
| 1.7 | 2026-03-23 | **`§4.3`** に `backend/` パッケージ・ルーター対応表を追加。**§7.1** の Ollama／プリセット／http_utils／passwords の所在を **`main.py` 単体記述から更新**。**§8** に Streamlit と `presets_io` / `storage` の共有を追記。**§2** 冒頭の API 説明は維持 |
| 1.8 | 2026-03-26 | **`§11.1`** に GT-2222 HTTPS サブパス公開の確定運用（`VITE_BASE_PATH`、`VITE_API_BASE`、`MM_CORS_ORIGINS`、frontend Nginx のプレフィックス剥がし、デプロイスクリプト運用）を追記 |
| 1.9 | 2026-03-26 | **`§11.1`** に TLS 運用（`gt-2222.crt`/`gt-2222.key`、`rootCA.crt` のクライアント手動ルート、同一443 `server` での証明書共有）を追記。`gt2222_https_subpath_troubleshooting.md` の証明書切り分けを拡充 |
| 1.10 | 2026-03-26 | **§6・§11.1** と `gt2222_https_subpath_troubleshooting.md` にブラウザ通知の許可ポップアップ運用を追記 |
| 1.11 | 2026-03-27 | **§8** に `streamlit_app/` パッケージ構成を追記（`app.py` の整理）。**`tasks.py`** は `celery_app` の冗長エイリアス削除と **`_assemble_prompt_with_context`** への抽出・統合 |
| 1.12 | 2026-03-27 | **§6** Ollama モデル候補は初回マウント・認証更新時のみ取得（ページ再読み込みで更新。フォーカス等での再取得なし） |
| 1.13 | 2026-03-27 | **`GET /api/ollama/models`** を**認証不要**に変更（認証 ON かつ未ログインで 401→一覧空になっていた問題の修正）。**§5** の応答説明を更新。**`ollama_client`** で tags の **`model`** キーにもフォールバック |
| 1.14 | 2026-03-27 | **§6** Ollama モデル UI を**コンボボックスから `<select>` のみ**に変更（手入力不可） |
| 1.15 | 2026-03-27 | **§5.1**・**§7.1**・**§11** に Ollama **`num_ctx: 4096`**（ワーカー `call_llm`）・**600 秒タイムアウト**、CLI **`pipeline/02_extract.py` / `03_merge.py`** との整合を追記（VRAM／KV 負荷・CPU オフロード抑制のため `8192` から変更） |
| 1.19 | 2026-03-28 | **コードに合わせて再同期**: **`GET /api/auth/status`** の拡張フィールド、**`ollama_client` と `tasks.call_llm` の役割分担**、**`try_ollama_unload_model` / `OLLAMA_UNLOAD_ON_TASK_END`**、**マージ失敗フォールバック**（`completed`・アンロード非呼び出し）、**`@celery_app.task`**、**`extract_json_block` は tasks と 02_extract に重複**（§2・§4.2・§4.3・§5・§5.1・§6・§7.1・§11） |
| 1.20 | 2026-03-28 | **議事録保存期限**: **`MM_MINUTES_RETENTION_DAYS`** 既定 **30 日**（約1か月）、**183 → 30 読み替え**、**`minutes_retention_days` / purge**（§7.1・§11）。**`AuthStatusResponse`** に **`minutes_retention_days`**・**`error_report_available`**（§5）。**§5.1** に **`transcript_only`**・**`whisper_preset`**。**§6** にヘルプ、アーカイブ見出し下の保存期間説明、Whisper 品質 UI、**`<select>` の `blur()`** |
| 1.21 | 2026-03-28 | **`GET /api/records`**: クエリ **`limit` / `offset`**、レスポンス **`{ items, total }`**（**`RecordsPageResponse`**）。**§6** 議事録アーカイブに **10 件ページング**・前へ次へ・フィルタ変更時のリセット。**§7.1** に **`count_recent_records` / `get_recent_records` のページング**。**Streamlit は `limit` 省略で全件**の旨を §5 に追記 |
| 1.22 | 2026-04-03 | **`POST /api/tasks`** に **`supplementary_teams` / `supplementary_notes`**。**§5.1** の **`num_ctx`** 説明を **`design_spec.md` §3.1.2** とプロファイル上書きに接続。**§8**・**§4.3** の **`storage` / `merge_task_prompt_paths`**。**§11** に **`MM_SUPPLEMENTARY_MAX_CHARS`**・**`MM_OLLAMA_PROFILES_PATH`** |
| 1.23 | 2026-04-03 | **`GET /api/queue`**: 認証時 **全ユーザーの待機・処理中**を返す（**`job_owner` / `is_mine`**）。**`get_active_queue_records_global`** |
| 1.24 | 2026-04-09 | **§5.2**・**§6**・**§7.1**: **`usage_job_log`** のメトリクス列（**`input_bytes`** ほか）と **`UsageMetricsRollup`** / **`GET /api/admin/usage/summary` の `metrics_rollup`**、管理者 UI の「負荷・容量の目安」・イベント表の列。ワーカー **`update_usage_job_metrics`** |
| 1.25 | 2026-04-10 | **§11.2** を追加。要件ID（RQ-01〜RQ-10）で **実装・テスト・品質報告（`coverage_report_2026-04-10.md`）** のトレーサビリティを明示 |
