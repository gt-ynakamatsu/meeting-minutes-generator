#!/usr/bin/env bash
#
# rsync でサーバへ同期 → リモートで docker compose 再ビルド・再起動
# UTF-8 / LF で保存
#
# 使い方:
#   ./scripts/deploy.sh              # ynakamatsu@GT-2222:~/meeting-minutes-generator/
#   ./scripts/deploy.sh user@host
#   DEPLOY_PATH=/path/on/server ./scripts/deploy.sh
#
# 環境変数: DEPLOY_HOST / DEPLOY_USER / DEPLOY_PATH
# 別の ssh バイナリ: DEPLOY_SSH=/path/to/ssh
#
# DEPLOY_USE_TAR=1 … ローカルで tar.gz 化し 1 本の ssh ストリームで展開（小ファイル多い・遅延大きい回線で速くなることがある）。
#   Docker ビルド時間やデーモンへのコンテキスト量は変わらない。リモートでローカルから消したファイルは残る（rsync --delete 相当ではない）。
#
# リモートでは scripts/server-rebuild.sh を実行（compose の --rmi local のみ。ホスト全体の prune は既定ではしない。data は削除しない）。
#

set -eu

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_BIN="${DEPLOY_SSH:-ssh}"

_DEFAULT_USER="${DEPLOY_USER:-ynakamatsu}"

if [[ "${1:-}" == *"@"* ]]; then
  REMOTE="${1}"
elif [[ -n "${1:-}" ]]; then
  REMOTE="${_DEFAULT_USER}@${1}"
else
  REMOTE="${_DEFAULT_USER}@${DEPLOY_HOST:-GT-2222}"
fi

TILDE_DEFAULT='~/meeting-minutes-generator'
if [[ -n "${DEPLOY_PATH:-}" ]]; then
  RDIR="${DEPLOY_PATH%/}"
  RSYNC_DEST="${REMOTE}:${RDIR}/"
  CD_PREFIX="cd $(printf '%q' "$RDIR")"
else
  RSYNC_DEST="${REMOTE}:${TILDE_DEFAULT}/"
  CD_PREFIX="cd ${TILDE_DEFAULT}"
fi

RSYNC=(
  rsync -avz
  -e "${SSH_BIN}"
  --exclude '.git/'
  --exclude 'frontend/node_modules/'
  --exclude '**/node_modules/'
  --exclude 'data/'
  --exclude 'downloads/'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '.venv/'
)

# tar 側は GNU tar の「名前が node_modules のディレクトリはどこでも除外」に寄せる（rsync の **/node_modules/ に相当）
TAR_EXCLUDES=(
  --exclude='.git'
  --exclude='data'
  --exclude='downloads'
  --exclude='__pycache__'
  --exclude='.venv'
  --exclude='node_modules'
  --exclude='*.pyc'
  --exclude='*.pt'
)

if [[ "${DEPLOY_USE_TAR:-}" == "1" ]]; then
  echo "==> tar.gz | ssh → ${REMOTE}（単一ストリーム・リモートの余剰ファイルは消えません）"
  if [[ -n "${DEPLOY_PATH:-}" ]]; then
    _RD="${DEPLOY_PATH%/}"
    _REMOTE_PREP="mkdir -p $(printf '%q' "${_RD}") && cd $(printf '%q' "${_RD}")"
  else
    _REMOTE_PREP='mkdir -p ~/meeting-minutes-generator && cd ~/meeting-minutes-generator'
  fi
  tar czf - -C "${ROOT}" "${TAR_EXCLUDES[@]}" . | "${SSH_BIN}" "${REMOTE}" bash -lc "${_REMOTE_PREP} && tar xzf -"
else
  echo "==> rsync ${ROOT}/  -->  ${RSYNC_DEST}"
  "${RSYNC[@]}" "${ROOT}/" "${RSYNC_DEST}"
fi

REMOTE_REBUILD="bash scripts/server-rebuild.sh"

echo "==> ${REMOTE}: ${REMOTE_REBUILD}（転送が巨大ならリモートに .dockerignore があるか確認）"
"${SSH_BIN}" "${REMOTE}" bash -lc "${CD_PREFIX} && ${REMOTE_REBUILD}"

echo "==> done"
