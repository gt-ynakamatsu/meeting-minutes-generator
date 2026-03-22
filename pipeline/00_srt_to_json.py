import json
import os
import re
import sys

# 設定
INPUT_FILE = os.path.join("input", "transcript.srt")
OUTPUT_FILE = os.path.join("input", "whisper_result.json")

def parse_srt_timestamp(ts_str):
    """
    SRT timestamp (00:00:01,000) to seconds (float)
    """
    try:
        hours, minutes, seconds_ms = ts_str.split(':')
        seconds, milliseconds = seconds_ms.split(',')
        total_seconds = int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000.0
        return total_seconds
    except ValueError:
        return 0.0

def parse_srt(content):
    """
    Parse SRT content string into segments list
    """
    segments = []
    
    # Regex to find blocks: Index \n Timestamp --> Timestamp \n Text
    # Note: Text can be multi-line
    pattern = re.compile(r'(\d+)\s+(\d{2}:\d{2}:\d{2},\d{3})\s-->\s(\d{2}:\d{2}:\d{2},\d{3})\s+((?:(?!\n\n).)*)', re.DOTALL)
    
    # Normalize Newlines
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    
    matches = pattern.findall(content)
    
    if not matches:
        print("Warning: No SRT segments found matching the pattern.")
        return []

    for idx, start_ts, end_ts, text in matches:
        start_sec = parse_srt_timestamp(start_ts)
        end_sec = parse_srt_timestamp(end_ts)
        clean_text = text.strip().replace('\n', ' ')
        
        segments.append({
            "id": int(idx),
            "start": start_sec,
            "end": end_sec,
            "text": clean_text
        })
        
    return segments

def main():
    # 1. Check Input
    if not os.path.exists(INPUT_FILE):
        print(f"Error: Input file not found: {INPUT_FILE}")
        sys.exit(1)
        
    print(f"Reading {INPUT_FILE}...")
    
    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading input file: {e}")
        sys.exit(1)

    # 2. Parse
    segments = parse_srt(content)
    print(f"Parsed {len(segments)} segments.")
    
    if not segments:
        print("Error: content parsing failed (0 segments).")
        sys.exit(1)

    # 3. Create Output JSON
    output_data = {
        "text": " ".join([s["text"] for s in segments]), # optional full text
        "segments": segments,
        "language": "ja" # assume japanese
    }

    # 4. Save
    try:
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"Success! Saved to {OUTPUT_FILE}")
    except Exception as e:
        print(f"Error saving output file: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
