#!/usr/bin/env bash
#
# プロジェクトを tar.gz に固めて scp → リモートで解凍（compose はしない）
# UTF-8 / LF で保存
#
# 使い方:
#   ./scripts/tar-scp.sh
#   ./scripts/tar-scp.sh user@host
#   DEPLOY_PATH=/path/on/server ./scripts/tar-scp.sh
#
# 環境変数: DEPLOY_HOST / DEPLOY_USER / DEPLOY_PATH / DEPLOY_SSH
# 出力ファイルを固定: TAR_SCP_OUT=/tmp/mm.tgz ./scripts/tar-scp.sh
# ローカル .tar.gz を残す: TAR_SCP_KEEP_LOCAL=1 ./scripts/tar-scp.sh
# 解凍後にリモートのアーカイブを削除: TAR_SCP_RM_REMOTE=1 ./scripts/tar-scp.sh
# 転送のみ（解凍しない）: TAR_SCP_SKIP_EXTRACT=1 ./scripts/tar-scp.sh
#
# 解凍は上書きマージのみ。ローカルで消したパスはリモートに残ることがあります（rsync --delete 相当ではない）。
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

# リモートコマンドは ssh の既定シェル向けに 1 引数で渡す（bash -lc は環境によって -c が空になり mkdir オペランド欠落の原因になる）。
# ~ はリモートの sh ではクォート内で展開されないことがあるため $HOME を使う。
TILDE_DEFAULT='~/meeting-minutes-generator'
if [[ -n "${DEPLOY_PATH:-}" ]]; then
  _RD="${DEPLOY_PATH%/}"
  [[ -n "${_RD}" ]] || {
    echo "error: DEPLOY_PATH が空です（末尾スラッシュのみや / だけは使えません）" >&2
    exit 1
  }
  _REMOTE_DIR_Q="$(printf '%q' "${_RD}")"
  _REMOTE_MKDIR="mkdir -p ${_REMOTE_DIR_Q}"
  _REMOTE_CD="cd ${_REMOTE_DIR_Q}"
  REMOTE_DIR="${_RD}"
else
  _REMOTE_MKDIR='mkdir -p "$HOME/meeting-minutes-generator"'
  _REMOTE_CD='cd "$HOME/meeting-minutes-generator"'
  REMOTE_DIR="${TILDE_DEFAULT}"
fi

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

STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE_BASENAME="${TAR_SCP_BASENAME:-meeting-minutes-generator-${STAMP}.tar.gz}"

if [[ -n "${TAR_SCP_OUT:-}" ]]; then
  LOCAL_TAR="${TAR_SCP_OUT}"
  _RM_LOCAL=0
else
  LOCAL_TAR="${TMPDIR:-/tmp}/mm-src-${STAMP}-$$.tar.gz"
  _RM_LOCAL=1
fi

REMOTE_TARGET="${REMOTE}:${REMOTE_DIR}/${ARCHIVE_BASENAME}"

SCP_CMD=(scp)
if [[ -n "${DEPLOY_SSH:-}" ]]; then
  SCP_CMD+=(-S "${SSH_BIN}")
fi

echo "==> ${REMOTE}: リモートディレクトリ作成"
"${SSH_BIN}" "${REMOTE}" "${_REMOTE_MKDIR}"

echo "==> tar czf ${LOCAL_TAR} (excludes: .git data node_modules …)"
tar czf "${LOCAL_TAR}" -C "${ROOT}" "${TAR_EXCLUDES[@]}" .

echo "==> scp → ${REMOTE_TARGET}"
"${SCP_CMD[@]}" "${LOCAL_TAR}" "${REMOTE_TARGET}"

if [[ -z "${TAR_SCP_SKIP_EXTRACT:-}" ]]; then
  _EXTRACT="${_REMOTE_CD} && tar xzf $(printf '%q' "${ARCHIVE_BASENAME}")"
  if [[ "${TAR_SCP_RM_REMOTE:-}" == "1" ]]; then
    _EXTRACT+=" && rm -f $(printf '%q' "${ARCHIVE_BASENAME}")"
  fi
  echo "==> ${REMOTE}: 解凍 ${ARCHIVE_BASENAME}"
  "${SSH_BIN}" "${REMOTE}" "${_EXTRACT}"
else
  echo "==> 解凍スキップ（TAR_SCP_SKIP_EXTRACT=1）"
fi

if [[ "${_RM_LOCAL}" -eq 1 && -z "${TAR_SCP_KEEP_LOCAL:-}" ]]; then
  rm -f "${LOCAL_TAR}"
  echo "==> done（ローカル一時ファイルは削除済み）"
else
  echo "==> done（ローカル: ${LOCAL_TAR}）"
fi
