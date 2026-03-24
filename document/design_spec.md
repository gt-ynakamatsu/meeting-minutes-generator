# AI議事録作成・アーカイブ (AI Minutes Archive) 基本設計書

## 1. はじめに

### 1.1 目的
本システムは、社内会議の動画・音声ファイルをAIを用いて自動的に文字起こし・要約し、構造化された議事録としてアーカイブすることを目的とする。これにより、議事録作成の工数削減と、情報の透明性・検索性の向上を図る。

### 1.2 背景
従来の議事録作成は手作業に依存しており、担当者の負担が大きく、品質にもばらつきがあった。また、作成されたファイルが各個人のPCに散在し、情報共有がスムーズに行われないという課題があった。本システムはこれらの課題を解決するための社内ツールである。

## 2. システム概要

### 2.1 機能一覧
| カテゴリ | 機能名 | 説明 |
| :--- | :--- | :--- |
| **ユーザー** | ファイルアップロード | 動画(mp4, m4a)・音声(mp3, wav)ファイルをドラッグ&ドロップでアップロード可能。 |
| | タスク状況確認 | 処理中のタスク（文字起こし中、要約中など）の進捗状況をプログレスバーで表示。 |
| | 議事録閲覧 | 作成完了した議事録をWebブラウザ上で閲覧可能。 |
| | ダウンロード | 議事録(Markdown形式)および全文テキスト(Text形式)をダウンロード可能。 |
| | 通知設定 | 完了通知をブラウザ通知またはWebhook（Slack/Chatwork等）で受け取り可能。 |
| **AI処理** | 自動文字起こし | Whisperモデルを使用し、高精度な音声認識を行う。 |
| | 構造化要約 | Ollama (Qwen2.5) を使用し、決定事項・課題・アクション・メモに自動分類・整理する。 |
| **管理** | 履歴管理 | 過去の議事録をデータベースで一元管理。 |
| | 自動クリーンアップ | 処理完了後の中間ファイル（音声・動画）を自動削除し、ストレージを節約。 |
| **認証** | 初回セットアップ | `MM_AUTH_SECRET` 有効時、ユーザー 0 件なら Web で最初の管理者（**メールアドレス・パスワード**）を登録。 |
| | ログイン | JWT（Bearer）。以降 API は認証ユーザーに紐づく議事録 DB を使用。 |
| | ユーザー・権限管理 | 管理者のみ：設定ドロワー内の専用タブでユーザー追加、パスワード再設定、管理者権限の付与・解除、削除（最後の管理者は保護）。 |

### 2.2 システムアーキテクチャ

本システムは、Dockerコンテナ上で動作する マイクロサービス構成に近いアーキテクチャを採用している。

```mermaid
graph TD
    User((User)) -->|Upload| Web["Web UI React"]
    Web -->|api| API["FastAPI"]
    API -->|Enqueue| Redis["Redis"]
    API -->|Read Write| DB[("SQLite")]
    
    subgraph wc["Worker container"]
        Worker["Celery worker"]
        Worker -->|Fetch| Redis
        Worker -->|Audio| MoviePy["MoviePy"]
        Worker -->|ASR| Whisper["Faster Whisper GPU"]
        Worker -->|LLM| Ollama["Ollama"]
        Worker -->|Status| DB
    end

    Ollama -->|Models| Models[("Model files")]
```

### 2.3 使用技術スタック
*   **Frontend**: Streamlit (Python)
*   **Backend Task Queue**: Celery
*   **Message Broker**: Redis
*   **Database**: SQLite (簡易実装、将来的なPostgreSQL移行を考慮)
*   **AI Engine**:
    *   ASR (Speech-to-Text): faster-whisper (Compute Type: float16, Device: CUDA)
    *   LLM (Summarization): Ollama (Model: qwen2.5:7b)
*   **Infrastructure**: Docker, NVIDIA Container Toolkit

## 3. データフロー設計

### 3.1 議事録作成パイプライン
処理は以下のステップで実行される。

1.  **受付**: ユーザーがファイルをアップロードし、UUIDが発行される。
2.  **音声抽出**: `moviepy` を使用して、動画ファイルから音声(MP3)を抽出。
3.  **文字起こし**: `faster-whisper` により音声データをテキスト化。タイムスタンプ付きのセグメントデータ (`segments`) を生成。
4.  **チャンク分割**: コンテキスト長を考慮し、セグメントを約75秒ごとのチャンクに結合。
5.  **情報抽出 (Map)**: 各チャンクに対してLLM (Ollama) を実行し、以下の要素をJSON形式で抽出。
    *   決定事項 (Decisions)
    *   課題 (Issues)
    *   アクションアイテム (Items/Actions)
    *   重要メモ (Notes)
6.  **統合 (Reduce)**: 全チャンクの抽出結果をマージし、再度LLMを実行して重複排除・文章の整形で最終的なMarkdown議事録を生成。
7.  **完了・通知**: データベースを更新し、Webhookまたはブラウザ経由でユーザーに完了を通知。

### 3.2 データベース設計 (簡易スキーマ)

**Tasks Table**
| カラム名 | 型 | 説明 |
| :--- | :--- | :--- |
| `id` | TEXT (PK) | タスク固有のUUID |
| `email` | TEXT | 依頼者のメールアドレス |
| `filename` | TEXT | アップロードされたファイル名 |
| `status` | TEXT | 現在のステータス (queued, processing:..., completed, error) |
| `transcript` | TEXT | 文字起こし全文 |
| `summary` | TEXT | 最終的な議事録データ (JSON/Markdown) |
| `created_at` | TIMESTAMP | 作成日時 |

## 4. インターフェース設計

### 4.1 画面構成（React + FastAPI 構成時）
1.  **認証**（`MM_AUTH_SECRET` 設定時）
    *   初回: 初回セットアップ（管理者ユーザー・パスワード）
    *   2 回目以降: ログイン
    *   管理者: 右上アイコンメニューから「ユーザー・権限管理」→ 設定ドロワーでユーザー運用
2.  **サイドバー (左側)**
    *   新規解析依頼フォーム（通知設定、ファイルアップローダー）
    *   OpenAI 利用時はログインユーザー専用 API キー設定（該当時）
3.  **メインエリア (右側)**
    *   **ヘッダー**: タイトル・右上アカウントアイコン（ドロップダウン：設定・ユーザー権限・サインアウト等）
    *   **議事録一覧**: 直近の履歴をエクスパンダー形式でリスト表示。
        *   展開時: プレビュー／編集／書き起こし、ダウンロードボタン

### 4.2 秘密情報と設定（外部に出さないこと）

*   **JWT 署名鍵**（`MM_AUTH_SECRET`）、**ブートストラップ用パスワード**（`MM_BOOTSTRAP_ADMIN_PASSWORD`）、**利用者の OpenAI API キー**（`registry.db` 保存分）は、リポジトリ・静的フロントのビルド成果物・スクリーンショット・公開ログに含めない。
*   **`VITE_*` 環境変数**はクライアント JS に埋め込まれるため、上記の秘密を渡さない（API の公開 URL のみ）。
*   ポート番号や CORS オリジンは「秘密」ではないが、**不要な外向き公開**は避ける。
*   コード上の所在、禁止事項、リリース前チェックリストの詳細は **`document/frontend_backend_design.md` の §7.1〜7.4** を参照する。

### 4.3 出力フォーマット (Markdown)
```markdown
#### 決定事項
- [決定内容] (根拠/発言者)

#### 課題
- [課題内容]

#### アクション
- [ ] **[担当者]**: [タスク内容] (期限: [期限])

#### 重要メモ
- [メモ内容]
```

## 5. デプロイ要件

*   **OS**: Linux または Windows (WSL2推奨)
*   **コンテナランタイム**: Docker Engine
*   **GPU**: NVIDIA GPU (CUDA対応) 必須
    *   VRAM: 8GB以上推奨 (Whisper Medium + Qwen2.5 7Bの同時稼働のため)
*   **ドライバ**: NVIDIA Driver, NVIDIA Container Toolkit

## 6. 付録：ディレクトリ構成
*   `app.py`: フロントエンド実装
*   `tasks.py`: バックエンド/パイプライン実装
*   `database.py`: DB操作ラッパー
*   `pipeline/`: ローカル実行用スクリプト群
*   `prompts/prompt_extract.txt`, `prompts/prompt_merge.txt`: プロンプトテンプレート

---
*Last Updated: 2026-03-23（§4.2 秘密情報の扱いを追加し出力例を §4.3 へ。認証をメールログインに合わせて記述）*
