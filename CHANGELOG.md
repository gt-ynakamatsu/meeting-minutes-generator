# Changelog

## 2.3.6

- **api**: `GET /api/records` がクエリ `limit` / `offset` に対応し、本文を **`{ "items", "total" }`** で返す（**破壊的変更**: 従来の配列直返しではない）。`RecordsPageResponse`（`backend/schemas.py`）。Streamlit は `limit` 省略で全件のまま
- **database**: `count_recent_records`、および `get_recent_records` の **`limit` / `offset`**（省略時は従来どおり全件）
- **frontend**: 議事録アーカイブを **1 ページ 10 件**、超過時は **前へ／次へ**。`listRecords` が `items` / `total` を解釈
- **frontend**: `tsconfig.json` の **`jsx`: `preserve`**（Vite 任せ。型解決で `react/jsx-runtime` を不要に）
- **docs**: README・`document/frontend_backend_design.md` に上記を反映

## 2.3.5

- **frontend**: 解析ファイルを枠クリックで選択・ドラッグ＆ドロップ対応、アイコンと案内文言、大きめドロップゾーン。アーカイブ一覧は約 5 件相当の高さでスクロール。レイアウトで上段（投入・キュー）を拡げ下段アーカイブをコンパクトに。ヒーロー説明文を実装に合わせて更新

## 2.3.4

- **frontend (Nginx)**: `index.html` のキャッシュ抑止と `/assets/` の長期キャッシュを設定（再デプロイ後の真っ白画面の予防）。README にトラブルシューティングを追記

## 2.3.3

- **server-rebuild**: イメージ掃除を **`docker compose down --rmi local`（この compose のビルドイメージのみ）**に限定。ホスト全体の `docker image prune` / `builder prune` は既定では実行しない（必要なら `GLOBAL_IMAGE_PRUNE` / `GLOBAL_BUILDER_PRUNE`）

## 2.3.2

- **運用**: サーバ向け `scripts/server-rebuild.sh`（Windows は `scripts/server-rebuild.bat`）— 再ビルド・起動。`down -v` や `volume prune` は行わず **`data` / `downloads` を保持**
- **deploy.sh**: リモート実行を `server-rebuild.sh` に統一

## 2.3.1

- **Whisper / VRAM**: 文字起こしを環境変数で調整可能に（`WHISPER_MODEL`・`WHISPER_DEVICE`・`WHISPER_COMPUTE_TYPE`）。CUDA OOM 時の緩和策を README / `.env.example`・UI トラブルシューティングに追記

## 2.3.0

- **プロンプト配置**: 既定テンプレートを `prompts/prompt_extract.txt`・`prompts/prompt_merge.txt` に集約（`tasks.py`・worker `Dockerfile`・ローカル `pipeline/` の参照を更新）
- **スクリプト**: `cleanup` を `scripts/cleanup.sh` / `scripts/cleanup.bat` に移動（実行時にリポジトリルートへ移動してから `docker-compose` 等を実行）。配布用 ZIP は `scripts/package_zip.py`
- **整理**: `pipeline/` 内の重複プロンプトとレガシー `archive/tasks_v1.py` を削除

## 2.2.2

- **環境変数の整理**: `.env.example` 追加、`docker-compose` の公開ポート・CORS を `MM_FRONTEND_PORT` / `MM_CORS_ORIGINS` で上書き可能に
- **CLI パイプライン** `pipeline/02_extract.py`・`03_merge.py` の Ollama URL を `OLLAMA_BASE_URL`、モデルを `OLLAMA_MODEL` で指定可能に（既定は従来どおりローカル 11434）
- **Vite**: リポジトリ直下 `.env` の `VITE_DEV_API_PROXY` で API プロキシ先を変更可能に

## 2.2.1

- React UI のダウンロードを **data URL から API 経由**に変更（`GET .../export/minutes`・`.../export/transcript`）。長い書き起こしでもブラウザの URL 長制限にかかりにくい

## 2.2.0

- **フロントエンド／バックエンド分離**: React（Vite）+ FastAPI（`backend/`）。本番は Nginx（`frontend/nginx.conf`）が静的配信と `/api` プロキシを担当
- **`celery_app.py`**: API が `tasks.py`（Torch 等）を import せず `send_task` するための軽量 Celery アプリ
- **Docker**: `Dockerfile.api`・`Dockerfile.frontend`・`docker-compose.yml` を `api` / `frontend` / `worker` 構成に更新（UI はポート **8085**）
- **設計書**: `document/frontend_backend_design.md`
- **レガシー**: `app.py`（Streamlit）はリポジトリに残置（任意で単体起動可能）

## 2.1.0

- 会議メタ情報（議題・分類・タグ・開催日）と、精度向上用コンテキスト（目的・参加者・用語・トーン・アクションルール）を DB に保存し LLM プロンプトへ注入
- 会議タイププリセット（`presets_builtin.json`）を選択可能に
- 動画・音声に加え、**テキスト (.txt) / 字幕 (.srt)** からの議事録作成（Whisper スキップ）。タイムコードが無い長文は文字数チャンクで分割
- アーカイブのキーワード・分類・ステータスフィルタ、処理キュー一覧、議事録の手直し保存（上書き）、エラー時トラブルシューティング表示

## 2.0.0

- UI をモダン化（タイポグラフィ・ヒーロー・サイドバー構成の整理）
- デザイン用アセット置き場 `assets/svg/`・`assets/images/` を追加
- Ollama / OpenAI の切り替えとモデル指定に対応
- 抽出・統合用プロンプト（議事録フォーマット）のテキストアップロードに対応
- アプリバージョンを `version.py` と画面フッターで表示
- Ollama 接続先を環境変数 `OLLAMA_BASE_URL` で上書き可能に統一
