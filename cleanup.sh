#!/bin/bash

echo "========================================================"
echo " WARNING: このスクリプトは以下の環境を完全に削除します:"
echo " 1. このプロジェクトのDockerコンテナとイメージ"
echo " 2. すべてのボリューム (データベース, Ollamaモデル)"
echo " 3. ローカルデータフォルダ (data, downloads, ollama_data)"
echo ""
echo " 過去の議事録データはすべて失われます。"
echo "========================================================"
echo ""
read -p "本当に実行しますか? (y/n): " confirm
if [[ "$confirm" != "y" ]]; then
    exit 0
fi

echo ""
echo "Dockerリソースを停止・削除しています..."
docker-compose down --rmi all -v --remove-orphans

echo ""
echo "ローカルデータを削除しています..."
if [ -d "data" ]; then
    rm -rf "data"
    echo "data フォルダを削除しました。"
fi
if [ -d "downloads" ]; then
    rm -rf "downloads"
    echo "downloads フォルダを削除しました。"
fi
if [ -d "ollama_data" ]; then
    rm -rf "ollama_data"
    echo "ollama_data フォルダを削除しました。"
fi

echo ""
echo "削除が完了しました。"
