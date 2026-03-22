# Changelog

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
