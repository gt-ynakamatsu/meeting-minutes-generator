#!/usr/bin/env bash
#
# プロジェクトを tar.gz に固めて scp → リモートで解凍（compose はしない）
# UTF-8 / LF で保存
#
# 解凍後はサーバで手動:  cd <プロジェクト直下>
#   docker compose down
#   docker compose up -d --build
# （deploy.sh を使わない運用向け。--build なしだとイメージが古く 502 / キュー待ちのままになりやすい）
# 詳細は README「tar.gz で転送して手動 docker compose」参照。
#
# 使い方:
#   ./scripts/tar-scp.sh
#   ./scripts/tar-scp.sh user@host
#   DEPLOY_PATH=/path/on/server ./scripts/tar-scp.sh
#
# 環境変数: DEPLOY_HOST / DEPLOY_USER / DEPLOY_PATH / DEPLOY_SSH
# 出力ファイルを固定: TAR_SCP_OUT=/tmp/mm.tgz ./scripts/tar-scp.sh
# ローカル .tar.gz を残す: TAR_SCP_KEEP_LOCAL=1 ./scripts/tar-scp.sh
# 既定では解凍後にリモートのアーカイブを削除（残したい場合）:
#   TAR_SCP_KEEP_REMOTE=1 ./scripts/tar-scp.sh
# 互換: TAR_SCP_RM_REMOTE=0 でも削除を抑止
# 転送のみ（解凍しない）: TAR_SCP_SKIP_EXTRACT=1 ./scripts/tar-scp.sh
#
# docker compose が読む .env は .gitignore のため tar に含まれないことが多い。
# 解凍後にリモートで GT-2222 用テンプレを .env にする:
#   TAR_SCP_SET_ENV=gt2222 ./scripts/tar-scp.sh
# （既存のリモート .env は上書きされる。ローカルに .env があり tar に含まれる場合も最後に上書き）
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

if [[ ! -f "${ROOT}/.env" ]]; then
  echo "==> 注意: ローカルに .env がありません（gitignore のため tar にも含まれません）。" >&2
  echo "    サーバで VITE_* が空のまま frontend がビルドされます。対策: ローカルで .env を作るか、" >&2
  echo "    TAR_SCP_SET_ENV=gt2222 を付けて解凍後に config/gt-2222.env を .env にコピーしてください。" >&2
fi

echo "==> tar czf ${LOCAL_TAR} (excludes: .git data node_modules …)"
tar czf "${LOCAL_TAR}" -C "${ROOT}" "${TAR_EXCLUDES[@]}" .

echo "==> scp → ${REMOTE_TARGET}"
"${SCP_CMD[@]}" "${LOCAL_TAR}" "${REMOTE_TARGET}"

if [[ -z "${TAR_SCP_SKIP_EXTRACT:-}" ]]; then
  _EXTRACT="${_REMOTE_CD} && tar xzf $(printf '%q' "${ARCHIVE_BASENAME}")"
  # 既定は「解凍後にリモート tar を削除」。
  # 明示的に残したい場合のみ抑止する。
  _RM_REMOTE_AFTER_EXTRACT=1
  if [[ "${TAR_SCP_KEEP_REMOTE:-}" == "1" || "${TAR_SCP_RM_REMOTE:-}" == "0" ]]; then
    _RM_REMOTE_AFTER_EXTRACT=0
  fi
  if [[ "${_RM_REMOTE_AFTER_EXTRACT}" -eq 1 ]]; then
    _EXTRACT+=" && rm -f $(printf '%q' "${ARCHIVE_BASENAME}")"
  fi
  echo "==> ${REMOTE}: 解凍 ${ARCHIVE_BASENAME}"
  "${SSH_BIN}" "${REMOTE}" "${_EXTRACT}"
  if [[ "${TAR_SCP_SET_ENV:-}" == "gt2222" ]]; then
    echo "==> ${REMOTE}: .env ← config/gt-2222.env（TAR_SCP_SET_ENV=gt2222）"
    _ENV_CMD="${_REMOTE_CD} && if [ -f config/gt-2222.env ]; then cp -f config/gt-2222.env .env; else echo 'error: config/gt-2222.env がありません' >&2; exit 1; fi"
    "${SSH_BIN}" "${REMOTE}" "${_ENV_CMD}"
  fi
else
  echo "==> 解凍スキップ（TAR_SCP_SKIP_EXTRACT=1）"
fi

if [[ "${_RM_LOCAL}" -eq 1 && -z "${TAR_SCP_KEEP_LOCAL:-}" ]]; then
  rm -f "${LOCAL_TAR}"
  echo "==> done（ローカル一時ファイルは削除済み）"
else
  echo "==> done（ローカル: ${LOCAL_TAR}）"
fi
