#!/usr/bin/env bash
#
# サーバー上で実行: このリポジトリの compose スタックだけ止めて古いビルドイメージを外し → 再ビルド → 起動
#
# ホスト全体の docker image prune / builder prune は既定では実行しません（他アプリのイメージ・キャッシュを消さないため）。
# イメージ削除は docker compose down --rmi local のみ（この compose でビルドした api / frontend / worker 相当）。
#
# データベース・議事録: ./data と ./downloads は bind mount のため削除しません。
# 実行しないこと: docker compose down -v / docker volume prune / docker system prune --volumes
#
# 使い方（docker-compose.yml があるディレクトリがカレントになるようスクリプトが cd）:
#   ./scripts/server-rebuild.sh
#
# 環境変数:
#   SKIP_RMI_LOCAL=1           … down 時の --rmi local を付けない（古いビルドイメージを残す）
#   COMPOSE_BUILD_PULL=1       … build 時に --pull
#   GLOBAL_IMAGE_PRUNE=1       … 非推奨: ホスト全体の dangling 削除（docker image prune -f）
#   GLOBAL_BUILDER_PRUNE=1     … 非推奨: ホスト全体のビルドキャッシュ削除（docker builder prune -f）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

if [[ ! -f docker-compose.yml ]]; then
  echo "Error: docker-compose.yml が見つかりません（カレント: ${ROOT}）" >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "Error: docker compose または docker-compose が見つかりません" >&2
  exit 1
fi

echo "==> 対象ディレクトリ: ${ROOT}"
echo "==> この compose プロジェクトのみ操作します。data/ downloads は触りません。-v は付けません。"

DOWN_ARGS=(--remove-orphans)
if [[ "${SKIP_RMI_LOCAL:-}" != "1" ]]; then
  DOWN_ARGS+=(--rmi local)
  echo "==> コンテナ停止 + このプロジェクトのローカルビルドイメージ削除（--rmi local）"
else
  echo "==> SKIP_RMI_LOCAL=1 … コンテナ停止のみ（--rmi local なし）"
fi

"${COMPOSE[@]}" down "${DOWN_ARGS[@]}"

if [[ "${GLOBAL_IMAGE_PRUNE:-}" == "1" ]]; then
  echo "==> GLOBAL_IMAGE_PRUNE=1 … ホスト全体: docker image prune -f（他スタックの dangling にも影響し得ます）"
  docker image prune -f
fi

if [[ "${GLOBAL_BUILDER_PRUNE:-}" == "1" ]]; then
  echo "==> GLOBAL_BUILDER_PRUNE=1 … ホスト全体: docker builder prune -f"
  docker builder prune -f
fi

BUILD_ARGS=()
if [[ "${COMPOSE_BUILD_PULL:-}" == "1" ]]; then
  BUILD_ARGS+=(--pull)
fi

echo "==> docker compose build"
"${COMPOSE[@]}" build "${BUILD_ARGS[@]}"

echo "==> docker compose up -d"
"${COMPOSE[@]}" up -d

echo "==> 完了（必要なら docker compose ps / docker compose logs -f）"
echo "==> 補足: redis は永続ボリューム無し。down 中はメモリ上のキューがリセットされます。"
