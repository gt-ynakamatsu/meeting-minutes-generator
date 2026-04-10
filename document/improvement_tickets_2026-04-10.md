# 改善チケット一覧（2026-04-10）

## 方針

- 2026-04-10 のシステムテスト実行（`python -m pytest`）で検出された warning を、再発防止できる単位でチケット化する。
- 優先度は「本番影響の近さ」と「将来の壊れやすさ」で決定する。

## チケットサマリ

| Ticket ID | タイトル | 優先度 | 状態 |
|---|---|---|---|
| MM-TEST-001 | SQLite 日時アダプタ非推奨対応 | 高 | Open |
| MM-TEST-002 | JWT テスト秘密鍵長の是正 | 中 | Open |
| MM-TEST-003 | warning 監視の自動化（CI/ローカル） | 中 | Open |

## MM-TEST-001: SQLite 日時アダプタ非推奨対応

- 背景:
  - `database.py` の `datetime` 直接書き込み・比較で、Python 3.12 以降の `sqlite3` 既定 datetime adapter 非推奨 warning が多発している。
- スコープ:
  - `database.py`
  - `tests/test_database_core.py`（必要に応じて期待値更新）
- 作業内容:
  - 日時カラムの入出力を明示フォーマット（ISO8601 文字列）に統一する。
  - `datetime.now()` 直接投入箇所を、保存用ユーティリティ経由へ置き換える。
  - 比較クエリのパラメータも同フォーマットへ揃える。
- 受け入れ条件:
  - `python -m pytest tests/test_database_core.py` で該当 DeprecationWarning が 0 件。
  - 既存機能（一覧、期限 purge、キュー取得）の回帰がない。
- 影響/リスク:
  - DB の日時比較ロジックに影響。既存データ互換性を維持するため、移行または読み取り時の後方互換処理を要検討。

## MM-TEST-002: JWT テスト秘密鍵長の是正

- 背景:
  - `tests/test_runtime_misc.py` の `test_auth_jwt_create_decode` で短い鍵（`"sec"`）を利用しており、`InsecureKeyLengthWarning` が発生している。
- スコープ:
  - `tests/test_runtime_misc.py`
- 作業内容:
  - テスト用固定シークレットを 32 bytes 以上に変更する。
  - 必要なら共通 fixture 化して再利用する。
- 受け入れ条件:
  - `python -m pytest tests/test_runtime_misc.py` 実行時に `InsecureKeyLengthWarning` が 0 件。
  - JWT encode/decode の期待挙動（`sub`、`iat`、`exp`）は維持される。
- 影響/リスク:
  - テストコードのみ。プロダクション挙動への影響はなし。

## MM-TEST-003: warning 監視の自動化（CI/ローカル）

- 背景:
  - warning が増えても気づきにくく、将来的に Python 更新時にテスト健全性が下がる可能性がある。
- スコープ:
  - テスト実行手順ドキュメント
  - CI 定義（存在する場合）
- 作業内容:
  - warning 件数を定期的に確認する実行手順を `README.md` または `document/` に明記。
  - 可能なら `pytest` の warning 制御（例: 主要 warning を fail 扱い）を段階導入。
- 受け入れ条件:
  - warning を「把握できる」仕組みが文書化され、再現手順が 1 コマンドで示される。
  - 既存テストが過剰に不安定化しない設定で運用開始できる。
- 影響/リスク:
  - いきなり全 warning を fail にすると運用負荷が上がるため、段階導入が必要。

## 実施順（推奨）

1. MM-TEST-002（テストコードのみ、即時解消）
2. MM-TEST-001（本命、warning の主要因）
3. MM-TEST-003（運用定着）

