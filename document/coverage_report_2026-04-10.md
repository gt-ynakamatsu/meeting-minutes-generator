# カバレッジ計測報告書（2026-04-10）

## 0. 関連ドキュメント（導線）

- 基本設計: `document/design_spec.md`
- 詳細設計: `document/frontend_backend_design.md`
- アーキテクチャ: `document/architecture_design.md`
- 本報告書（品質エビデンス）: `document/coverage_report_2026-04-10.md`

## 1. 報告概要

- 目的: 本番コードのテスト網羅性を定量確認し、100%達成状況を記録する。
- 対象: `app.py` / `backend/` / `database.py` / `tasks.py` / `feature_flags.py` / `streamlit_app/` / `celery_app.py`。
- 実施日: 2026-04-10
- 実施環境: Windows 10 (`win32 10.0.26200`), Python 3.12.3, `pytest 9.0.3`, `pytest-cov 7.1.0`

## 2. 結果サマリ

### 2.1 計測結果（最終）

- テスト総数: **129 passed**
- 本番コードカバレッジ（対象限定）: **100% (2926/2926)**
- リポジトリ全体（`--cov=.`、tests含む）: **99%**

### 2.2 判定

- 判定: **合格（本番コード100%を達成）**
- 補足: 99%の未達分は `tests/` 側の未到達行であり、本番コードには未達行なし。

## 3. テスト内容とテストコードの紐づけ（実装トレーサビリティ）


| 要件ID | 対象機能/モジュール | 対応実装 | 対応テストコード | 主な検証内容 |
| :--- | :--- | :--- | :--- | :--- |
| RQ-01 | ジョブ処理（正常・異常・キャンセル） | `tasks.py` | `tests/test_tasks_helpers.py` | 正常系（transcript-only/議事録生成）、失敗系（抽出失敗/統合失敗）、通知分岐、キャンセル分岐、GPU/メモリ解放、補助関数、例外経路。 |
| RQ-02 | 利用状況ログ（管理者） | `database.py`, `backend/routes/admin.py`, `backend/routes/jobs.py`, `app.py` | `tests/test_database_core.py`, `tests/test_admin_routes_core.py`, `tests/test_jobs_routes.py`, `tests/test_app_smoke.py` | `usage_job_log` 記録、メトリクス更新、集計API、認可、入力検証、運用メモCRUD。 |
| RQ-03 | 目安箱（投稿/管理） | `backend/routes/feedback.py`, `database.py`, `backend/schemas.py` | `tests/test_feedback_routes.py`, `tests/test_admin_suggestion_routes.py`, `tests/test_suggestion_box.py`, `tests/test_suggestion_schemas.py` | 投稿、Webhook通知、管理者一覧/更新、スキーマ妥当性。 |
| RQ-04 | 認証/権限管理 | `backend/routes/auth.py`, `backend/auth_settings.py`, `backend/deps.py`, `database.py` | `tests/test_auth_routes.py`, `tests/test_runtime_misc.py`, `tests/test_backend_misc.py`, `tests/test_database_core.py` | JWT発行/検証、ログイン、初期管理者、管理者判定、ガード分岐。 |
| RQ-05 | タスク作成API | `backend/routes/jobs.py`, `backend/schemas.py` | `tests/test_jobs_routes.py` | 入力検証、OpenAI/Ollama分岐、Celery投入引数、異常系レスポンス。 |
| RQ-06 | 議事録一覧/詳細/更新 | `backend/routes/records.py`, `database.py` | `tests/test_records_routes.py`, `tests/test_database_core.py` | 一覧取得、詳細、更新、認可/所有者判定、DB整合。 |
| RQ-07 | 通知（SMTP/Webhook） | `backend/smtp_notify.py`, `tasks.py`, `backend/routes/feedback.py` | `tests/test_smtp_notify.py`, `tests/test_tasks_helpers.py`, `tests/test_feedback_routes.py` | SMTP設定判定、送信経路、TLS/SSL、Webhook送信、失敗時ハンドリング。 |
| RQ-08 | LLM/Ollama連携 | `backend/ollama_client.py`, `backend/ollama_model_profiles.py`, `tasks.py` | `tests/test_ollama_modules.py`, `tests/test_tasks_helpers.py` | モデル一覧取得、アンロード、プロファイル解決、HTTP/JSONエラー分岐。 |
| RQ-09 | 起動/ライフサイクル | `backend/main.py`, `feature_flags.py` | `tests/test_backend_main.py`, `tests/test_backend_misc.py` | lifespan、ルータ初期化、機能フラグ適用。 |
| RQ-10 | Streamlit経路（レガシーUI） | `app.py`, `streamlit_app/` | `tests/test_app_smoke.py` | 主要画面遷移、ジョブ投入、通知設定、アーカイブ表示。 |


## 4. 実行コマンド

### 4.1 本番コードのみ計測

```bash
python -m pytest --maxfail=1 --disable-warnings \
  --cov=app --cov=backend --cov=database --cov=tasks \
  --cov=feature_flags --cov=streamlit_app --cov=celery_app \
  --cov-report=term
```

### 4.2 リポジトリ全体計測（tests含む）

```bash
python -m pytest --maxfail=1 --disable-warnings --cov=. --cov-report=term-missing
```

## 5. 計測手順（再現可能な運用手順）

1. 依存が未導入ならテストツールを導入
  `python -m pip install pytest pytest-cov`
2. リポジトリルートに移動（`meeting-minutes-generator/meeting-minutes-generator`）
3. まず本番コード限定計測を実行（4.1）
4. 次に全体計測を実行（4.2）
5. 判定基準
  - 本番コード対象で `Miss=0` を合格条件とする  
  - 参考値として全体カバレッジも記録
6. 結果を本報告書に追記し、日付付きで保管

## 5.1 計測対象・非対象の定義

- 計測対象（本番コード）: `app.py`, `backend/`, `database.py`, `tasks.py`, `feature_flags.py`, `streamlit_app/`, `celery_app.py`
- 非対象（参考値のみ）: `tests/`, `document/`, 補助スクリプト
- 判定ルール: 本番コード対象で `Miss=0` を合格基準とする

## 6. 主要な追加テスト観点（今回）

- `tasks.py` の複雑分岐（キャンセルタイミング、外側例外、失敗時後始末）を実行フロー単位で追加検証。
- 利用状況ログ（入力サイズ・媒体時間・Whisper/LLM処理時間・文字数）を反映する経路を、DB更新の副作用まで検証。
- 通知系（Webhook/SMTP）を設定有無・例外パス込みで検証。
- 管理者機能（利用状況・目安箱・運用メモ）の認可/入力/エラー経路を網羅。

## 7. 既知事項・注意点

- `--cov=.` の場合、`tests/` も分母に入るため 99% 表示になる。
- 本番品質判定は、本報告書では「本番コード対象の100%」を基準としている。
- テスト実行時に warning は発生するが、全テストの pass/fail 判定には影響していない。

## 8. 次回更新時のチェックリスト

- 新規モジュール追加時に `--cov` 対象へ含めたか
- 重要分岐（失敗・例外・キャンセル・通知失敗）を副作用込みで検証したか
- 本番コード対象で `Miss=0` を維持できているか
- 本報告書の日付と結果を更新したか

## 9. 変更管理情報

- 作成日: 2026-04-10
- 作成者: AI Assistant（Cursor）
- 対象コミット範囲: 作業ツリー未コミット差分を含む最新ローカル状態
- 更新ルール: カバレッジ再計測時は「2.結果」「3.トレーサビリティ」「5.手順/対象」「9.変更管理」を更新する

