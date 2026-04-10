# 社内専用：AI議事録作成・アーカイブ (AI Minutes Archive)

動画や音声ファイルをアップロードするだけで、AIが自動で文字起こしを行い、**構造化された詳細な議事録（Markdown形式）**を作成・アーカイブする社内専用アプリケーションです。

## 主な機能

*   **高品質AI議事録**: 「分割(Chunk) → 抽出(Extract) → 統合(Merge)」の3段階パイプラインを採用。長時間の会議でも文脈を見失わず、決定事項・課題・ネクストアクションを漏れなく抽出します。
*   **構造化フォーマット**: 議事録は Markdown 形式で出力され、決定事項・課題・アクション・重要メモに分類されます。
*   **マルチフォーマット対応**: 動画/音声ファイルだけでなく、SRT（字幕ファイル）やテキストファイルからの議事録作成もサポートしています。
*   **自動文字起こし**: OpenAI製の高性能音声認識モデル [Faster Whisper](https://github.com/guillaumekln/faster-whisper) を使用し、GPU活用で高速・高精度にテキスト化します。
*   **アーカイブ機能**: 過去の議事録をデータベースに保存し、ブラウザ上で閲覧・Markdown（`.md`）ダウンロード。**保存期限**は **`MM_MINUTES_RETENTION_DAYS`**（既定 **90 日**。環境変数・挙動は下表と設計書 `document/frontend_backend_design.md` §7.1）。
*   **完全ローカル処理**: データはすべて社内のサーバー内で処理・保存されるため、機密情報が外部に漏れる心配はありません。
*   **モデル切り替え**: 要約・統合は Ollama（モデル名指定可）または OpenAI API から選択できます。
*   **フォーマット差し替え**: 社内テンプレに合わせて `prompt_extract` / `prompt_merge` 相当の `.txt` をアップロードして利用できます（プレースホルダはアプリ内ヘルプ参照）。
*   **バージョン表示**: アプリの版は `version.py`（`__version__`）および画面フッターで確認できます。変更履歴は `CHANGELOG.md` を参照してください。リリースや運用・UI に目に見える変更が入ったときは、版を上げて `CHANGELOG.md` に要約を追記します（内部のみの軽微な修正では省略しても構いません）。
*   **会議メタ＆コンテキスト**: 議題・分類・タグに加え、目的・参加者・用語・トーンなどを入力すると抽出・統合プロンプトに反映されます。
*   **参考資料追加（任意）**: 解析画面で補助資料を追加できます。**Teams 等のトランスクリプトは `.vtt` のみ**、**担当メモは `.txt` / `.md`** に対応します。
*   **プリセット**: `presets_builtin.json` の会議タイプ（定例・顧客・1on1 等）を選べます。追記・編集で社内用に拡張可能です。
*   **テキスト / SRT 入力**: 文字起こし済みの `.txt` や `.srt` だけでも議事録生成できます（Whisper をスキップ）。
*   **書き起こしのみ**: チェックで **Whisper（または .txt/.srt 読み取り）まで**とし、議事録用 LLM（抽出・統合）は実行しません。
*   **アップロード上限と推奨**: 解析ファイルの上限は **5GB**。**2GB 超は事前分割**を推奨します。API では投入レート制限（429）と `downloads` の空き容量ガード（503）を有効にしています。
*   **音声認識の品質（Whisper）**: **高速 / 標準 / 高精度**（`whisper_preset`）で faster-whisper の探索の強さを切り替え（動画・音声のみ）。**既定は高精度**（UI・API の `TaskSubmitMetadata`・ワーカー未指定時のフォールバック）。
*   **アーカイブ**: 検索・フィルタ、処理キュー表示、議事録の手直し保存に対応。見出し下に **保存期間の説明**（サーバの `minutes_retention_days` と一致）を表示します。**議事録一覧は 1 ページ最大 10 件**で、それを超える件数があるときは **前へ／次へ** でページ送りします（HTTP API は **`GET /api/records`** の **`limit` / `offset`** と **`{ items, total }`** 応答。詳細は設計書）。
*   **管理者向け利用状況（認証有効時）**: **`MM_AUTH_SECRET` 有効**かつ **管理者**のみ、右上メニューの **「利用ログ画面」** からジョブ投入の集計を閲覧できます。**議事録本文・書き起こし全文・ファイル名は記録しません**（拡張子から推定した媒体種別、パイプライン種別、Ollama/OpenAI とモデル名、Whisper プリセット、通知方式、参考資料添付の有無、ログイン ID、タスク ID 等）。**完了ジョブ**については入力ファイルサイズ・媒体の長さ・音声抽出／Whisper／議事録 LLM の処理時間・書き起こし文字数などの**メトリクス**も参照できます（サーバ強化・稟議の根拠用。サマリの集計はメトリクス記録済みのジョブに限定）。さらに **防御イベント**（`rate_limited` / `upload_too_large` / `disk_low`）を記録し、`GET /api/admin/usage/settings-summary` で設定利用傾向と合わせて確認できます。集計期間は **最大 365 日（1 年）**。運用メモの追記・削除に対応。API は **`GET /api/admin/usage/*`**（設計書 `document/frontend_backend_design.md` §5.2）。Streamlit から投入したジョブも認証有効なら同様に記録されます（メール未入力時はユーザー列が空になり得ます）。
*   **ヘルプ**: メイン画面の **ヘルプ**（`#help`）から **`HelpPage`** を開き、操作手順に加え **ブラウザ通知の有効化**・**サイト設定**・**HTTPS / 社内 CA 証明書のインストール（Windows）**・**利用状況ログの扱い（プライバシー）** などを参照できます。
*   **デスクトップ通知**: 通知先 **ブラウザ** を選ぶと、ジョブ完了時に OS 通知を出せます（Chromium 系は **安全なページ**＝多くの場合 **HTTPS** または **localhost / 127.0.0.1** が必要。`http://` の IP や社内ホスト名のみでは無効になりやすい）。詳細はアプリ内ヘルプの「ブラウザ通知を有効にする手順」を参照してください。
*   **フロント／API 分離**: UI は **React（`frontend/`）**、HTTP API は **FastAPI（`backend/`）**。構成・エンドポイントは **[設計書 `document/frontend_backend_design.md`](document/frontend_backend_design.md)** を参照。
*   **UI デザインモック（静的）**: Docker なしで確認する場合は **`design/ui-mockup.html`** をブラウザで開く（Windows は `design/open-mockup.bat`）。

## 前提条件

*   **Docker** および **Docker Compose**（`docker compose` コマンドが使えること）
*   **NVIDIA GPU**（推奨: VRAM 8GB 以上）— **worker**（Whisper 等）用。**Ollama** は別途 Docker 等で起動し、本リポジトリの Compose では立てません
*   **NVIDIA Container Toolkit**（コンテナから GPU を使うため）

## セットアップと起動方法

### 1. リポジトリの取得
```bash
git clone <repository-url>
cd meeting-minutes-generator
```
または `meeting-minutes-generator.zip` を解凍してください。

### 2. Docker を起動する**前**にやること（環境依存・準備）

ここから下は **コンテナを立ち上げる前**に、各自のマシン／運用に合わせて揃えます。

| 区分 | 内容 |
|------|------|
| **ホスト OS・ハード** | GPU ドライバ、NVIDIA Container Toolkit、十分なディスク（Ollama モデル・Whisper キャッシュ用）。 |
| **ネットワーク `llm-net`（必須）** | **worker** が外部ネットワーク **`llm-net`** にも参加し、そこにいる **Ollama**（例: コンテナ名 **`ollama-server`**）へ接続します。初回のみ `docker network create llm-net`（未作成時）。Ollama がまだ `llm-net` にいない場合は `docker network connect llm-net <Ollamaのコンテナ名>`。未作成のまま `docker compose up` するとエラーになります。 |
| **Ollama（別途）** | LLM 用の **Ollama は本 Compose に含めません**。**`llm-net` 上の DNS 名**で届く想定で、ワーカー既定は **`http://ollama-server:11434`**。コンテナ名が違う場合は `.env` の **`OLLAMA_BASE_URL`** で指定してください。 |
| **ポート（環境依存）** | 既定ではブラウザ **`http://localhost:8085`** で UI にアクセスします。8085 が既に使われている場合は、起動**前**にプロジェクト直下で `.env` を用意し **`MM_FRONTEND_PORT`** を変更してください（`.env.example` 参照）。 |
| **CORS（本番・社内 URL）** | ブラウザから **別ホスト名・HTTPS** で API にアクセスする場合は、起動**前**に **`MM_CORS_ORIGINS`**（カンマ区切り）にそのオリジンを含めてください。ローカルだけなら既定のままで可。 |
| **Webhook（任意）** | 完了通知を使う場合は **`.env` の `WEBHOOK_URL`** に実 URL を書く（`docker-compose.yml` は `${WEBHOOK_URL}` を参照）。未使用ならプレースホルダのままで可。 |
| **議事録の保持期限（任意）** | **`MM_MINUTES_RETENTION_DAYS`** … 未指定時 **90**（日）。**0 以下**で自動削除なし。api / worker に渡す（`docker-compose.yml` の **`:-90`**）。 |
| **メール通知（任意）** | UI で「メール」を選べるのは **`MM_SMTP_HOST` と `MM_SMTP_FROM`** 等が設定されているとき。完了メールの送信は **Celery ワーカー**が行うため、**api と worker の両方**に同じ SMTP 環境変数を渡す（`.env.example` 参照）。ログイン時は通知先の既定が **ログイン ID（メール）**。 |
| **.env（任意）** | プロジェクト直下の `.env` を Compose が自動読み込み。**GT-2222 では既定として `config/gt-2222.env` をコピー**するのがおすすめ（`cp config/gt-2222.env .env`）。汎用テンプレは `.env.example`。 |
| **ログイン認証** | **`MM_AUTH_SECRET`** があると JWT ログインが有効になり、**ユーザーごとに `data/user_data/.../minutes.db` に議事録が分離**されます。`docker compose` では未指定時も **既定のフォールバック秘密鍵で認証 ON**（本番は `openssl rand -hex 32` 等で必ず差し替え）。初回はブラウザの初回セットアップまたは `MM_BOOTSTRAP_ADMIN_*` で管理者を作成。 |

**Compose が起動時に自動で行うもの（事前準備不要）**

*   `redis` などのイメージ取得、`data`・`downloads` 用のディレクトリマウント（ホスト側に空で作成される）

**Docker 起動「後」**（手順 4）：**別途の Ollama** で要約用モデルの **`ollama pull`** を実行してください。

### 3. コンテナのビルドと起動
プロジェクト直下（`docker-compose.yml` があるディレクトリ）で実行します。
```bash
docker compose up -d --build
```
* 初回はイメージビルドと Whisper 用モデルのダウンロードに時間がかかることがあります。

**GT-2222** … ホスト向けの既定値は **`config/gt-2222.env`**。`cp config/gt-2222.env .env` のうえ、上記と同じく `docker compose up -d --build` で可。8085 が旧 Streamlit（`whisper-ui`）と重なる場合は、先に一方を止めるか `.env` の **`MM_FRONTEND_PORT`** を変更してください。

#### tar.gz でサーバへ送って手動 `docker compose`（`deploy.sh` を使わない場合）

`scripts/deploy.sh` や rsync を使わず、**tar で固めて転送 → 解凍 → 自分で `down` / `up --build`** する運用で問題ありません。

1. **送る側**: `scripts/tar-scp.sh`（または同等の `tar czf`）。このスクリプトは **docker compose を実行しません**。
2. **サーバ側**: 解凍後、**必ずプロジェクト直下**で次のいずれかを実行してください。
   - **掃除付き（推奨）**: `docker compose down --rmi local` で **このプロジェクトでビルドしたイメージだけ**外してから再ビルドします。ホスト全体の `docker image prune` / `builder prune` は既定では行いません（**`./data`・`./downloads` は削除しません**）。
     ```bash
     bash scripts/server-rebuild.sh
     ```
   - **手動**:
     ```bash
     docker compose down
     docker compose up -d --build
     ```
   ソースだけ更新して **`--build` を付けない**と、コンテナ内のコード／イメージが古いままになり、**502** や **ジョブがキュー待ちのまま**（ワーカーが起動できていない）などにつながります。

`scripts/deploy.sh` は同期後、リモートで **`scripts/server-rebuild.sh`** と同じ手順を実行します。

- **新規ファイル**（例: `backend/smtp_notify.py`）がアーカイブに含まれているか確認してください。**メール通知を UI で選ばなくても API は起動時に `backend` 配下のモジュールを読み込みます**。欠けると API やワーカーが落ちます。
- `tar xzf` の上書き展開では、**送る側で消したパスがサーバから消えない**ことがあります（`tar-scp.sh` 先頭コメント）。挙動がおかしいときはサーバ上のツリーと差分を確認してください。

### 4. 要約用 AI モデルの準備（必須・起動後）
**別途起動している Ollama** で、要約に使うモデルを取得してください（コンテナ名は環境により異なります）。

```bash
docker exec ollama-server ollama pull qwen2.5:7b
```

※ 未実行だとワーカー側で「model not found」などのエラーになります。UI で別モデルを指定する場合は、その名前で `pull` してください。

### 5. アプリケーション利用
ブラウザで次にアクセスします（ポートを変えていなければ 8085）。Nginx 経由で React と `/api` が同一オリジンです。

[http://localhost:8085](http://localhost:8085)

**ブラウザ通知**を使う場合は、左パネルで通知を **ブラウザ** にし、表示に従って **許可** してください。`http://192.168.x.x` 等では通知 API が無効になりやすいので、**HTTPS** 化するか **localhost / 127.0.0.1** で試すか、**Webhook・メール**に切り替えてください。手順・証明書（社内 CA）の詳細は画面の **ヘルプ** を開いてください。

**サブパス配信**（例: `/meetingminutesnotebook/`）では、フロント再ビルド前に **`.env` の `VITE_BASE_PATH` / `VITE_API_BASE`** を実 URL に合わせます（GT-2222 向けは `config/gt-2222.env` 参照。設計・TLS の詳細は `document/frontend_backend_design.md` §11.1、`document/gt2222_https_subpath_troubleshooting.md`）。

### 6. ローカル開発（API + フロントのみ）

* API: `pip install -r requirements-api.txt` のうえ `uvicorn backend.main:app --reload --port 8000`（プロジェクトルートで実行）
* フロント: `cd frontend && npm install && npm run dev`（Vite が `/api` を `http://127.0.0.1:8000` にプロキシ）
* ワーカー: 従来どおり `celery -A tasks worker`（`requirements.txt` フルセットの環境で）

### レガシー UI（Streamlit）

`app.py` は任意で利用できます（例: `streamlit run app.py`）。本番の推奨構成は上記 **8085** の React + FastAPI です。

### 環境変数一覧（`.env` / Compose と対応）

* **GT-2222 専用の既定値** … **`config/gt-2222.env`**（`docker ps` に合わせた値。Ollama は別 Docker・ホスト 11434 前提）。`cp config/gt-2222.env .env` で利用。社内ホスト名・Webhook だけ実環境に合わせて編集してください。
* **汎用テンプレ** … **`.env.example`**

いずれもプロジェクト直下の `.env` に置くと `docker compose` が読み込みます（**手順 2** と内容が対応）。

| 変数 | 用途 |
|------|------|
| `MM_FRONTEND_PORT` | ホストに公開するフロントのポート（既定 `8085`） |
| `MM_CORS_ORIGINS` | FastAPI の CORS 許可オリジン（カンマ区切り） |
| `CELERY_BROKER_URL` | Redis（Compose 内は `redis://redis:6379/0`） |
| `OLLAMA_BASE_URL` | LLM（Compose 内ワーカー既定は **`http://ollama-server:11434`**・`llm-net` 上のコンテナ名想定。CLI をホストで動かすなら `http://127.0.0.1:11434` 等） |
| `OLLAMA_MODEL` | CLI パイプライン `02_extract` / `03_merge` のモデル名（既定 `qwen2.5:7b`） |
| `WHISPER_MODEL` / `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | **worker のみ**。動画・音声の文字起こし（faster-whisper）。既定 `medium` / `cuda` / `float16`。VRAM 不足時は `small` や `int8_float16`、必要なら `cpu` + `int8`（遅い） |
| `WEBHOOK_URL` | 完了通知 Webhook |
| `MM_SMTP_HOST` / `MM_SMTP_FROM` | メール完了通知（必須ペア。他は `.env.example`） |
| `MM_AUTH_SECRET` | JWT 署名用秘密鍵。**未指定時は Compose 内蔵フォールバックで認証 ON**。本番は独自の長いランダム文字列を推奨 |
| `MM_BOOTSTRAP_ADMIN_USER` / `MM_BOOTSTRAP_ADMIN_PASSWORD` | 任意。API 起動時にユーザー 0 件なら最初の管理者を自動登録 |
| `MM_AUTH_TOKEN_HOURS` | JWT 有効時間（既定 168） |
| `MM_AUTH_SELF_REGISTER` | `1`（既定）で 1 人目以降が **自分で新規登録** 可能。`0` / `false` で無効（管理者追加のみ） |
| `MM_MINUTES_RETENTION_DAYS` | 議事録レコードの保持日数。**未指定時 90**。**183** は **90 日として扱う**（旧既定からの移行）。**0 以下**で自動削除オフ |
| `MM_OPENAI_ENABLED` | `0` / `false` 等で OpenAI 連携オフ（UI・API で Ollama のみ）。未設定時はオン扱い（後方互換） |
| `MM_UPLOAD_MAX_BYTES` | API 側のアップロード上限（バイト）。既定 **5 GiB**。Nginx 側上限（`client_max_body_size 5g`）と合わせて運用 |
| `MM_TASK_SUBMIT_RATE_LIMIT_COUNT` / `MM_TASK_SUBMIT_RATE_LIMIT_WINDOW_SEC` | API の投入レート制限。既定 **60 秒あたり 30 件**（`COUNT=0` で無効） |
| `MM_UPLOAD_WARN_FREE_GB` / `MM_UPLOAD_MIN_FREE_GB` | `downloads` の空き容量監視閾値（GiB）。既定は警告 **20**、受付拒否 **5** |
| `MM_EMAIL_NOTIFY_ENABLED` | `1` でメール通知を UI に出す（SMTP 必須）。詳細は `.env.example` |
| `VITE_BASE_PATH` / `VITE_API_BASE` | 本番フロントビルド時。**サブパス配信**で必須。末尾スラッシュの有無は `VITE_BASE_PATH` のみ（`VITE_API_BASE` は末尾なし推奨） |
| `VITE_ALT_APP_HOSTNAME` | 任意。通知案内などで **名前付き URL** へのリンクを出すときのホスト名 |
| `VITE_DEV_API_PROXY` | フロント開発時、`/api` のプロキシ先（既定 `http://127.0.0.1:8000`） |

`frontend/nginx.conf` の `api:8000` や Compose のサービス名 **`redis`** は **コンテナ間の DNS 名**であり、特定の実サーバー名を直書きしているわけではありません。
大きい素材はネットワーク断の再送コストが大きいため、**2GB 超は事前分割**を推奨します。

### 画面が真っ白になる（Docker 更新・再ビルド直後）

- **ブラウザのキャッシュ**: 古い `index.html` が残り、存在しない `/assets/*.js` を読みに行っていることが多い。**スーパーリロード**（Ctrl+Shift+R 等）かキャッシュ削除を試す。
- **Nginx**: `frontend/nginx.conf` では `index.html` を再検証させるヘッダを付与している。**フロントイメージを再ビルド**（`docker compose build frontend` または `server-rebuild.sh`）して反映する。
- **開発者ツール（F12）→ ネットワーク**: `index.html` や `/assets/` の JS が **404** になっていないか確認する。
- **API**: `docker compose ps` で **`mm-api`** が Up か。落ちていると API は 502 になりがちだが、通常は「読み込み中」のあとエラー表示になりやすい。

## ローカルパイプライン (上級者向け)

Webアプリを使わず、コマンドラインから議事録を作成することも可能です。長時間動画のバッチ処理や、音声認識済みのSRTファイルを利用する場合に便利です。

ディレクトリ: `./pipeline`

### 実行可能なスクリプト
*   `00_srt_to_json.py`: SRTファイルをパイプライン入力用に変換
*   `01_chunk.py`: テキストを一定時間(75秒)ごとに分割
*   `02_extract.py`: 各チャンクから決定事項などをAI抽出
*   `03_merge.py`: 抽出結果を統合して最終議事録(Markdown)を生成

### 使い方 (例)
```bash
cd pipeline
# input/ に whisper_result.json または transcript.srt を配置してから...

python3 00_srt_to_json.py  # (SRTの場合のみ)
python3 01_chunk.py
python3 02_extract.py   # Ollama URL は環境変数 OLLAMA_BASE_URL（既定 localhost:11434）
python3 03_merge.py
```
結果は `output/final_minutes.md` に出力されます。

## ディレクトリ構成

*   `frontend/`: **React（Vite）** SPA。本番は Nginx で `dist` を配信。ヘルプ本文は `frontend/src/HelpPage.tsx`（ブラウザ通知・証明書手順を含む）
*   `backend/`: **FastAPI**（`main.py`・`routes/`）。`GET /api/auth/status` に **`minutes_retention_days`** 等を返し、UI の保存期間表示と整合。`GET /api/records` は **`{ items, total }`** と任意の **`limit` / `offset`**（一覧ページング用）
*   `database.py`: SQLite・**議事録の保持期限**（`minutes_retention_days` / purge）・認証時の **`data/registry.db`**（ユーザー・**利用ログ** `usage_job_log`（メトリクス列含む） / 管理者メモ `usage_admin_notes`）・ユーザー別 `minutes.db`・**議事録一覧**（**`count_recent_records`** / **`get_recent_records`** のフィルタ・ページング）
*   `app.py`: Streamlit フロントエンド（レガシー。Markdown 表示・DL）
*   `tasks.py`: AI 議事録生成パイプライン（Celery ワーカー）
*   `pipeline/`: ローカル実行用スクリプト群
*   `prompts/prompt_extract.txt`: 抽出フェーズ用プロンプト
*   `prompts/prompt_merge.txt`: 統合フェーズ用プロンプト
*   `document/`: **設計書**（`frontend_backend_design.md`・`architecture_design.md`・`design_spec.md`）と **品質報告**（`coverage_report_2026-04-10.md`）
*   `scripts/`: デプロイ・クリーンアップ等の補助スクリプト

## 環境の削除
`scripts/cleanup.bat` (Windows) または `scripts/cleanup.sh` (Linux) を実行すると、データベースやログを含むすべてのデータを削除して初期化できます（リポジトリルートで `docker-compose` が動くよう、スクリプトは自動で親ディレクトリに移動します）。

## 配布用 ZIP（任意）
リポジトリルートで `python scripts/package_zip.py` を実行すると、除外ルール付きで `meeting-minutes-generator.zip` を生成します。
