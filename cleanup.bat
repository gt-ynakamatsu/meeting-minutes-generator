@echo off
chcp 65001 > nul
echo ========================================================
echo  WARNING: このスクリプトは以下の環境を完全に削除します:
echo  1. このプロジェクトのDockerコンテナとイメージ
echo  2. すべてのボリューム (データベース, Ollamaモデル)
echo  3. ローカルデータフォルダ (data, downloads, ollama_data)
echo.
echo  過去の議事録データはすべて失われます。
echo ========================================================
echo.
set /p confirm="本当に実行しますか? (y/n): "
if /i "%confirm%" neq "y" goto :eof

echo.
echo Dockerリソースを停止・削除しています...
docker-compose down --rmi all -v --remove-orphans

echo.
echo ローカルデータを削除しています...
if exist "data" (
    rmdir /s /q "data"
    echo data フォルダを削除しました。
)
if exist "downloads" (
    rmdir /s /q "downloads"
    echo downloads フォルダを削除しました。
)
if exist "ollama_data" (
    rmdir /s /q "ollama_data"
    echo ollama_data フォルダを削除しました。
)

echo.
echo 削除が完了しました。
pause
