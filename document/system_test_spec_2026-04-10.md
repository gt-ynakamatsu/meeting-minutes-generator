# システムテスト仕様書（2026-04-10）

## 1. 目的

- 本システム（React + FastAPI + Celery + SQLite）の主要業務フローが、利用者視点で破綻なく動作することを確認する。
- 既存の自動テスト群を「システムテスト」として再編成し、実行結果を記録可能な形にする。

## 2. 対象範囲

- 対象:
  - API 層（`backend/`）
  - ワーカー処理（`tasks.py`）
  - データ永続化（`database.py`）
  - レガシー UI の基本導線（`app.py`）
- 非対象:
  - Docker 本番デプロイ実機試験（別途運用試験）
  - 実際の GPU / Ollama 実体を使う負荷試験（本仕様は自動化テスト中心）

## 3. テスト方式

- 方式: `pytest` による自動実行（モックを含む統合寄りのシステム観点テスト）
- 実行ディレクトリ: `meeting-minutes-generator/meeting-minutes-generator`
- 合格基準:
  - 指定テストが全件 `passed`
  - 失敗 (`failed`) と実行時エラー (`error`) が 0 件

## 4. テスト環境

- OS: Windows 10 (`win32 10.0.26200`)
- Python: ローカル環境の `python`
- テストランナー: `pytest`

## 5. テスト項目一覧

| No | システムテスト観点 | 対応テストコード | 判定条件 |
|---|---|---|---|
| ST-01 | アプリ全体の起動スモークと画面主要分岐 | `tests/test_app_smoke.py` | 全件 pass |
| ST-02 | API 起動・ルーティング・共通ランタイム整合 | `tests/test_backend_main.py`, `tests/test_backend_misc.py`, `tests/test_runtime_misc.py` | 全件 pass |
| ST-03 | 認証（初期化・ログイン・権限） | `tests/test_auth_routes.py` | 全件 pass |
| ST-04 | ジョブ投入 API と入力バリデーション | `tests/test_jobs_routes.py` | 全件 pass |
| ST-05 | レコード API（一覧・更新・取得・出力） | `tests/test_records_routes.py` | 全件 pass |
| ST-06 | 管理者 API（利用状況・メモ） | `tests/test_admin_routes_core.py` | 全件 pass |
| ST-07 | 目安箱/フィードバック機能の投稿〜管理 | `tests/test_feedback_routes.py`, `tests/test_admin_suggestion_routes.py`, `tests/test_suggestion_box.py`, `tests/test_suggestion_schemas.py` | 全件 pass |
| ST-08 | DB コア機能（レジストリ・集計・クリーンアップ） | `tests/test_database_core.py` | 全件 pass |
| ST-09 | ワーカーパイプライン（正常/異常/通知分岐） | `tests/test_tasks_helpers.py` | 全件 pass |
| ST-10 | 外部連携境界（Ollama/SMTP） | `tests/test_ollama_modules.py`, `tests/test_smtp_notify.py` | 全件 pass |

## 6. 実行手順

1. プロジェクトルートへ移動する。  
   `cd meeting-minutes-generator/meeting-minutes-generator`
2. システムテストを実行する。  
   `python -m pytest`
3. 実行ログの `passed/failed/error` 件数を確認する。

## 7. 実施結果（2026-04-10）

- 実施コマンド: `python -m pytest`
- 実施結果: `129 passed, 50 warnings`
- 実行時間: `9.47s`
- 補足:
  - `failed=0`, `error=0`
  - warning の主因は `database.py` の `sqlite3` datetime adapter 非推奨通知、およびテスト内の短い JWT キー由来の警告

## 8. 判定

- 判定: 合格

## 9. フォローアップ（改善チケット）

- warning 是正のための改善チケットを起票済み: `document/improvement_tickets_2026-04-10.md`

