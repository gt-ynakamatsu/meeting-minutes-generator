from celery import Celery
import os
import requests
from faster_whisper import WhisperModel
import database as db
import torch
from moviepy.editor import VideoFileClip
import uuid

app = Celery('tasks', broker=os.getenv('CELERY_BROKER_URL'))

import re

def normalize_to_segments(input_data):
    """
    入力を標準的なセグメント辞書のリストに正規化する
    Output format: [{'start': float, 'end': float, 'text': str}, ...]
    """
    segments = []
    
    # helper to parse timestamp "00:00:00,000" -> seconds (float)
    def parse_srt_time(t_str):
        h, m, s_ms = t_str.split(':')
        s, ms = s_ms.split(',')
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    if isinstance(input_data, list):
        for item in input_data:
            # Whisper Segment Object
            if hasattr(item, 'start') and hasattr(item, 'end') and hasattr(item, 'text'):
                segments.append({
                    'start': float(item.start),
                    'end': float(item.end),
                    'text': item.text.strip()
                })
            # Dictionary (already normalized-ish)
            elif isinstance(item, dict) and 'text' in item:
                segments.append({
                    'start': float(item.get('start', 0.0)),
                    'end': float(item.get('end', 0.0)),
                    'text': item.get('text', '').strip()
                })
    
    elif isinstance(input_data, str):
        # SRT Format Check (Simple Regex)
        srt_pattern = re.compile(r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n((?:(?!\n\n).)*)', re.DOTALL)
        matches = srt_pattern.findall(input_data.replace('\r\n', '\n'))
        
        if matches:
            for _, start_str, end_str, text_content in matches:
                segments.append({
                    'start': parse_srt_time(start_str),
                    'end': parse_srt_time(end_str),
                    'text': text_content.strip().replace('\n', ' ')
                })
        else:
            # Plain Text (treat as single chunk or split by lines)
            # 簡易的に行ごとに分割し、タイムスタンプはダミーを入れるか、全体を1つにする
            # ここでは空行で分割してリスト化
            parts = input_data.split('\n\n')
            for i, p in enumerate(parts):
                if p.strip():
                    segments.append({
                        'start': 0.0, # Unknown
                        'end': 0.0,   # Unknown
                        'text': p.strip()
                    })

    return segments

@app.task
def process_video_task(task_id, email, filename, file_path, webhook_url=None):
    db.update_record(task_id, status="processing:extracting_audio")
    audio_path = os.path.join("downloads", f"{uuid.uuid4()}.mp3")
    try:
        # 音声抽出 (CPU)
        video = None
        try:
            video = VideoFileClip(file_path)
            video.audio.write_audiofile(audio_path, logger=None)
        finally:
            if video:
                video.close()
        
        # Whisper (GPU)
        db.update_record(task_id, status="processing:transcribing")
        model = WhisperModel("medium", device="cuda", compute_type="float16")
        raw_segments, _ = model.transcribe(audio_path)
        
        # 正規化
        segments = normalize_to_segments(list(raw_segments))
        
        # 90秒ごとにチャンク分け
        formatted_transcript = []
        current_chunk_text = []
        chunk_start = 0.0
        chunk_end = 0.0
        
        CHUNK_DURATION = 90.0
        
        for segment in segments:
            if not current_chunk_text:
                chunk_start = segment['start']
            
            current_chunk_text.append(segment['text'])
            chunk_end = segment['end']
            
            if (chunk_end - chunk_start) >= CHUNK_DURATION:
                # タイムスタンプ整形 (mm:ss)
                start_str = f"{int(chunk_start // 60):02d}:{int(chunk_start % 60):02d}"
                end_str = f"{int(chunk_end // 60):02d}:{int(chunk_end % 60):02d}"
                
                block = f"[{start_str} - {end_str}]\n" + "".join(current_chunk_text)
                formatted_transcript.append(block)
                
                current_chunk_text = []
                
        # 残りのテキスト
        if current_chunk_text:
            start_str = f"{int(chunk_start // 60):02d}:{int(chunk_start % 60):02d}"
            end_str = f"{int(chunk_end // 60):02d}:{int(chunk_end % 60):02d}"
            block = f"[{start_str} - {end_str}]\n" + "".join(current_chunk_text)
            formatted_transcript.append(block)

        transcript = "\n\n".join(formatted_transcript)
        del model
        torch.cuda.empty_cache()

        # Ollama (GPU)
        db.update_record(task_id, status="processing:summarizing")
        # Ollama (GPU) - Map-Reduce Pipeline
        db.update_record(task_id, status="processing:summarizing")
        
        # 1. Split (分割)
        chunks = []
        current_chunk = []
        current_length = 0
        MAX_CHUNK_SIZE = 4000 # 文字数目安
        
        for block in formatted_transcript:
            if current_length + len(block) > MAX_CHUNK_SIZE and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_length = 0
            
            current_chunk.append(block)
            current_length += len(block)
            
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        # 2. Map (抽出)
        partial_results = []
        import json
        
        for i, chunk in enumerate(chunks):
            try:
                # Update status for large files
                db.update_record(task_id, status=f"processing:summarizing ({i+1}/{len(chunks)})")
                
                res = requests.post("http://ollama:11434/api/generate", json={
                    "model": "qwen2.5:7b",
                    "prompt": f"""あなたは議事録作成の抽出エンジンです。推測・補完は禁止。
以下の会話ログ（タイムスタンプ付き）から、事実として言えるものだけを抽出してJSONで出力してください。

要件:
- 決定事項(decisions): 決まったこと。根拠timestampsを必ず付ける。
- 課題(issues): 未解決/懸念/詰まり。根拠timestamps必須。
- アクション(items): 誰が/何を/いつまで を可能な限り。無ければnull。根拠timestamps必須。
- 重要メモ(notes): 重要だが上に入らない要点。根拠timestamps必須。
- 口語の言い換えはOKだが、内容の捏造は禁止。

JSONスキーマ:
{{
  "decisions":[{{"text":"...", "evidence":["00:00:00-00:00:10", ...]}}],
  "issues":[{{"text":"...", "evidence":[...]}}],
  "items":[{{"who":"...", "what":"...", "due":"...", "evidence":[...]}}],
  "notes":[{{"text":"...", "evidence":[...]}}]
}}

--- 会話ログ (Part {i+1}/{len(chunks)}) ---
{chunk}""",
                    "format": "json",
                    "stream": False
                })
                
                if res.status_code == 200:
                    data = res.json().get("response", "{}")
                    partial_results.append(json.loads(data))
                else:
                    print(f"Error processing chunk {i}: {res.status_code}")
                    
            except Exception as e:
                print(f"Exception processing chunk {i}: {str(e)}")

        # 3. Reduce (統合)
        final_result = {
            "decisions": [],
            "issues": [],
            "items": [],
            "notes": []
        }
        
        for p in partial_results:
            if isinstance(p, dict):
                final_result["decisions"].extend(p.get("decisions", []))
                final_result["issues"].extend(p.get("issues", []))
                final_result["items"].extend(p.get("items", []))
                final_result["notes"].extend(p.get("notes", []))

        summary = json.dumps(final_result, ensure_ascii=False)
        
        # Notification
        db.update_record(task_id, status="processing:sending_notification")
        
        db.update_record(task_id, status="completed", transcript=transcript, summary=summary)
        
        # 通知処理
        final_webhook_url = webhook_url if webhook_url else os.getenv("WEBHOOK_URL")
        
        if email and final_webhook_url and final_webhook_url != "YOUR_WEBHOOK_URL_HERE":
            # JSON形式だと長すぎるかもしれないので、簡易的な通知にするか、リンクを送る
            msg = f"✅ **議事録作成完了**\nファイル: {filename}\n[アーカイブを確認]"
            try:
                requests.post(final_webhook_url, json={"text": msg, "email": email, "filename": filename})
            except Exception as e:
                print(f"Webhook notification failed: {e}")

    except Exception as e:
        db.update_record(task_id, status=f"Error: {str(e)}")
    finally:
        if os.path.exists(audio_path): os.remove(audio_path)
        if os.path.exists(file_path): os.remove(file_path)