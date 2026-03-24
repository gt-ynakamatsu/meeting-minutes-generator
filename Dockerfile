FROM pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# COPY . . だとコンテキストに残った巨大ファイルもレイヤに入るため明示コピー（Compose で ./:/app マウント時は実行時はホスト優先）
COPY tasks.py celery_app.py database.py presets_builtin.json version.py ./
COPY prompts ./prompts
COPY backend ./backend
RUN chmod +x /app