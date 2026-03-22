# 社内専用：AI議事録作成・アーカイブ (AI Minutes Archive)

動画や音声ファイルをアップロードするだけで、AIが自動で文字起こしを行い、**構造化された詳細な議事録（Markdown形式）**を作成・アーカイブする社内専用アプリケーションです。

## ✨ 主な機能

*   **高品質AI議事録**: 「分割(Chunk) → 抽出(Extract) → 統合(Merge)」の3段階パイプラインを採用。長時間の会議でも文脈を見失わず、決定事項・課題・ネクストアクションを漏れなく抽出します。
*   **構造化フォーマット**: 議事録はMarkdown形式で出力され、「決定事項(💡)」「課題(⚠️)」「アクション(🚀)」「重要メモ(📌)」に分類されます。
*   **マルチフォーマット対応**: 動画/音声ファイルだけでなく、SRT（字幕ファイル）やテキストファイルからの議事録作成もサポートしています。
*   **自動文字起こし**: OpenAI製の高性能音声認識モデル [Faster Whisper](https://github.com/guillaumekln/faster-whisper) を使用し、GPU活用で高速・高精度にテキスト化します。
*   **アーカイブ機能**: 過去の議事録をデータベースに保存し、ブラウザ上で閲覧・Markdown形式 (`.md`) でのダウンロードが可能です。
*   **完全ローカル処理**: データはすべて社内のサーバー内で処理・保存されるため、機密情報が外部に漏れる心配はありません。
*   **モデル切り替え**: 要約・統合は Ollama（モデル名指定可）または OpenAI API から選択できます。
*   **フォーマット差し替え**: 社内テンプレに合わせて `prompt_extract` / `prompt_merge` 相当の `.txt` をアップロードして利用できます（プレースホルダはアプリ内ヘルプ参照）。
*   **バージョン表示**: アプリの版は `version.py` および画面フッターで確認できます。変更履歴は `CHANGELOG.md` を参照してください。
*   **会議メタ＆コンテキスト**: 議題・分類・タグに加え、目的・参加者・用語・トーンなどを入力すると抽出・統合プロンプトに反映されます。
*   **プリセット**: `presets_builtin.json` の会議タイプ（定例・顧客・1on1 等）を選べます。追記・編集で社内用に拡張可能です。
*   **テキスト / SRT 入力**: 文字起こし済みの `.txt` や `.srt` だけでも議事録生成できます（Whisper をスキップ）。
*   **アーカイブ**: 検索・フィルタ、処理キュー表示、議事録の手直し保存に対応しています。
*   **フロント／API 分離**: UI は **React（`frontend/`）**、HTTP API は **FastAPI（`backend/`）**。構成・エンドポイントは **[設計書 `document/frontend_backend_design.md`](document/frontend_backend_design.md)** を参照。
*   **UI デザインモック（静的）**: Docker なしで確認する場合は **`design/ui-mockup.html`** をブラウザで開く（Windows は `design/open-mockup.bat`）。

## 🛠️ 前提条件

*   **Docker** および **Docker Compose**（`docker compose` コマンドが使えること）
*   **NVIDIA GPU**（推奨: VRAM 8GB 以上）— **worker**（Whisper 等）用。**Ollama** は別途 Docker 等で起動し、本リポジトリの Compose では立てません
*   **NVIDIA Container Toolkit**（コンテナから GPU を使うため）

## 🚀 セットアップと起動方法

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
| **.env（任意）** | プロジェクト直下の `.env` を Compose が自動読み込み。**GT-2222 では既定として `config/gt-2222.env` をコピー**するのがおすすめ（`cp config/gt-2222.env .env`）。汎用テンプレは `.env.example`。 |

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

### 4. 要約用 AI モデルの準備（必須・起動後）
**別途起動している Ollama** で、要約に使うモデルを取得してください（コンテナ名は環境により異なります）。

```bash
docker exec ollama-server ollama pull qwen2.5:7b
```

※ 未実行だとワーカー側で「model not found」などのエラーになります。UI で別モデルを指定する場合は、その名前で `pull` してください。

### 5. アプリケーション利用
ブラウザで次にアクセスします（ポートを変えていなければ 8085）。Nginx 経由で React と `/api` が同一オリジンです。

[http://localhost:8085](http://localhost:8085)

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
| `WEBHOOK_URL` | 完了通知 Webhook |
| `VITE_DEV_API_PROXY` | フロント開発時、`/api` のプロキシ先（既定 `http://127.0.0.1:8000`） |

`frontend/nginx.conf` の `api:8000` や Compose のサービス名 **`redis`** は **コンテナ間の DNS 名**であり、特定の実サーバー名を直書きしているわけではありません。

## 💻 ローカルパイプライン (上級者向け)

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

## 📂 ディレクトリ構成

*   `app.py`: Streamlit フロントエンド（Markdown表示・DL機能付き）
*   `tasks.py`: AI議事録生成パイプライン（Celeryワーカー）
*   `pipeline/`: ローカル実行用スクリプト群
*   `prompt_extract.txt`: 抽出フェーズ用プロンプト
*   `prompt_merge.txt`: 統合フェーズ用プロンプト
*   `archive/`: 古いバージョンのコード

## 🗑️ 環境の削除
`cleanup.bat` (Windows) または `cleanup.sh` (Linux) を実行すると、データベースやログを含むすべてのデータを削除して初期化できます。
