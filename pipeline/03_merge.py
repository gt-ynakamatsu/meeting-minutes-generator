import json
import os
import requests
import glob
import sys
import time

# --- 設定 ---
# ディレクトリ構成（README どおり `cd pipeline` 実行を想定）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT_DIR = os.path.join("work", "extracted")
OUTPUT_FILE = os.path.join("output", "final_minutes.md")
PROMPT_FILE = os.path.join(_REPO_ROOT, "prompts", "prompt_merge.txt")

# Ollama設定（tasks.py と同様に OLLAMA_BASE_URL で上書き）
def _ollama_generate_url():
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    return f"{base}/api/generate"


OLLAMA_URL = _ollama_generate_url()
MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# パラメータ設定 (選べるように変数化)
TEMPERATURE = 0
NUM_CTX = 4096  # 8192 は VRAM/CPU オフロード負荷が大きい。必要なら上げる
REQ_TIMEOUT = 600

def main():
    # 1. 前提チェック
    if not os.path.exists(PROMPT_FILE):
        print(f"Error: プロンプトファイルが見つかりません -> {PROMPT_FILE}")
        sys.exit(1)
        
    if not os.path.exists(INPUT_DIR):
        print(f"Error: 入力ディレクトリが見つかりません -> {INPUT_DIR}")
        sys.exit(1)

    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        prompt_template = f.read()

    # 2. JSONデータの収集と結合
    files = sorted(glob.glob(os.path.join(INPUT_DIR, "chunk_*.json")))
    if not files:
        print("処理対象のJSONファイル (work/extracted/chunk_*.json) がありません。")
        sys.exit(0)

    combined_data = {
        "decisions": [],
        "issues": [],
        "items": [],
        "notes": []
    }

    print(f"Merging {len(files)} extracted files...")
    
    valid_chunks = 0
    for file_path in files:
        # 読み込んで結合
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            if isinstance(data, dict):
                combined_data["decisions"].extend(data.get("decisions", []))
                combined_data["issues"].extend(data.get("issues", []))
                combined_data["items"].extend(data.get("items", []))
                combined_data["notes"].extend(data.get("notes", []))
                valid_chunks += 1
            else:
                print(f"Warning: JSON format invalid (not dict) -> {os.path.basename(file_path)}")
                
        except json.JSONDecodeError:
            print(f"Warning: JSON Decode Error -> {os.path.basename(file_path)}")
        except Exception as e:
            print(f"Warning: Read Error ({str(e)}) -> {os.path.basename(file_path)}")

    if valid_chunks == 0:
        print("有効なJSONデータが1つもありませんでした。処理を終了します。")
        sys.exit(1)

    # 3. リクエスト用JSON作成
    json_input_str = json.dumps(combined_data, ensure_ascii=False, indent=2)

    # プロンプト埋め込み
    prompt = prompt_template.replace("{EXTRACTED_JSON}", json_input_str)

    # 4. Ollama 呼び出し
    print(f"Generating minutes (Model: {MODEL_NAME}, Ctx: {NUM_CTX})...")
    print("AI処理中... (これには数分かかる場合があります)")

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": TEMPERATURE,
            "num_ctx": NUM_CTX
        }
    }
    
    try:
        start_time = time.time()
        res = requests.post(OLLAMA_URL, json=payload, timeout=REQ_TIMEOUT)
        elapsed = time.time() - start_time
        
        if res.status_code == 200:
            final_md = res.json().get("response", "")
            
            # ディレクトリなければ作成
            os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
            
            # 保存
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(final_md)
                
            print("-" * 40)
            print(f"完了 ({elapsed:.1f}s)")
            print(f"出力ファイル: {os.path.abspath(OUTPUT_FILE)}")
            print("-" * 40)
            
        else:
            print(f"FAIL: Ollama API returned {res.status_code}")
            print(f"Response: {res.text}")
            sys.exit(1)

    except requests.exceptions.Timeout:
        print("FAIL: Ollama request timed out.")
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: Unexpected error -> {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
