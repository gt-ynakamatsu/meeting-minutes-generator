@echo off
chcp 65001 > nul
setlocal EnableExtensions

cd /d "%~dp0.."

if not exist "docker-compose.yml" (
  echo Error: docker-compose.yml が見つかりません（カレント: %CD%）
  exit /b 1
)

echo ==^> 対象ディレクトリ: %CD%
echo ==^> この compose プロジェクトのみ操作します。data\ downloads は触りません。-v は付けません。

if "%SKIP_RMI_LOCAL%"=="1" (
  echo ==^> SKIP_RMI_LOCAL=1 … コンテナ停止のみ
  docker compose down --remove-orphans 2>nul
  if errorlevel 1 docker-compose down --remove-orphans 2>nul
) else (
  echo ==^> コンテナ停止 + このプロジェクトのローカルビルドイメージ削除（--rmi local）
  docker compose down --remove-orphans --rmi local 2>nul
  if errorlevel 1 docker-compose down --remove-orphans --rmi local 2>nul
)

if "%GLOBAL_IMAGE_PRUNE%"=="1" (
  echo ==^> GLOBAL_IMAGE_PRUNE=1 … ホスト全体: docker image prune -f
  docker image prune -f
)

if "%GLOBAL_BUILDER_PRUNE%"=="1" (
  echo ==^> GLOBAL_BUILDER_PRUNE=1 … ホスト全体: docker builder prune -f
  docker builder prune -f
)

if "%COMPOSE_FULL_NO_CACHE%"=="1" (
  echo ==^> docker compose build --no-cache（全サービス）
  docker compose build --no-cache
  if errorlevel 1 docker-compose build --no-cache
) else if "%SKIP_FRONTEND_NO_CACHE%"=="1" (
  if "%COMPOSE_BUILD_PULL%"=="1" (
    echo ==^> docker compose build --pull
    docker compose build --pull
    if errorlevel 1 docker-compose build --pull
  ) else (
    echo ==^> docker compose build
    docker compose build
    if errorlevel 1 docker-compose build
  )
) else (
  echo ==^> docker compose build --no-cache frontend
  docker compose build --no-cache frontend
  if errorlevel 1 docker-compose build --no-cache frontend
  if "%COMPOSE_BUILD_PULL%"=="1" (
    echo ==^> docker compose build --pull api worker
    docker compose build --pull api worker
    if errorlevel 1 docker-compose build --pull api worker
  ) else (
    echo ==^> docker compose build api worker
    docker compose build api worker
    if errorlevel 1 docker-compose build api worker
  )
)

echo ==^> docker compose up -d
docker compose up -d
if errorlevel 1 docker-compose up -d

echo ==^> 完了
echo ==^> 補足: redis は永続ボリューム無し。down 中はメモリ上のキューがリセットされます。
endlocal
