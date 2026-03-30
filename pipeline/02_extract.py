import json
import os
import requests
import glob
import re
import sys
import time

# --- 設定 ---
# ディレクトリ構成（README どおり `cd pipeline` 実行を想定）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT_DIR = os.path.join("work", "chunks")
OUTPUT_DIR = os.path.join("work", "extracted")
PROMPT_FILE = os.path.join(_REPO_ROOT, "prompts", "prompt_extract.txt")

# Ollama設定（Compose ワーカーでは OLLAMA_BASE_URL が注入される。CLI はホストの localhost 想定）
def _ollama_generate_url():
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    return f"{base}/api/generate"


OLLAMA_URL = _ollama_generate_url()
MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")


def _ollama_timeout():
    raw = (os.getenv("MM_OLLAMA_TIMEOUT_SEC") or "600").strip()
    try:
        read_sec = max(60, int(raw))
    except ValueError:
        read_sec = 600
    return (30, read_sec)

def extract_json_block(text):
    """
    テキストから最初の { ... } ブロックを抽出して JSON としてパースする。
    """
    text = text.strip()
    
    # 単純な JSON パース
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 前後に余計な文字がある場合の抽出 (最も外側の {} を探す)
    # 簡易的に最初の { と 最後の } を探す
    start = text.find("{")
    end = text.rfind("}")
    
    if start != -1 and end != -1 and end > start:
        json_str = text[start : end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
            
    return None

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

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 2. ファイル一覧取得
    files = sorted(glob.glob(os.path.join(INPUT_DIR, "chunk_*.txt")))
    if not files:
        print("処理対象のチャンクファイル (*.txt) がありません。")
        sys.exit(0)

    success_count = 0
    fail_count = 0
    total_files = len(files)

    print(f"開始: 合計 {total_files} ファイルを処理します...")
    print("-" * 40)

    # 3. ループ処理
    for i, file_path in enumerate(files, 1):
        basename = os.path.basename(file_path)
        filename_no_ext, _ = os.path.splitext(basename)
        output_path = os.path.join(OUTPUT_DIR, f"{filename_no_ext}.json")
        
        # 既に存在・成功していればスキップしたい場合はここに判定を入れるが、
        # 今回は上書き前提で進める（必要ならここを修正）
        
        print(f"[{i}/{total_files}] Processing {basename} ... ", end="", flush=True)
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                chunk_text = f.read()
            
            prompt = prompt_template.replace("{CHUNK_TEXT}", chunk_text)
            
            payload = {
                "model": MODEL_NAME,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {
                    "temperature": 0,
                    "num_ctx": 4096
                }
            }
            
            start_time = time.time()
            res = requests.post(OLLAMA_URL, json=payload, timeout=_ollama_timeout())
            elapsed = time.time() - start_time
            
            if res.status_code == 200:
                raw_response = res.json().get("response", "")
                
                # JSON抽出・パース
                parsed_data = extract_json_block(raw_response)
                
                if parsed_data is not None:
                    # 保存
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(parsed_data, f, ensure_ascii=False, indent=2)
                    
                    print(f"OK ({elapsed:.1f}s)")
                    success_count += 1
                else:
                    print(f"FAIL (Invalid JSON) ({elapsed:.1f}s)")
                    # デバッグ用に生レスポンスを保存
                    err_dump = output_path.replace(".json", ".err.txt")
                    with open(err_dump, "w", encoding="utf-8") as f:
                        f.write(raw_response)
                    fail_count += 1
            else:
                print(f"FAIL (HTTP {res.status_code})")
                fail_count += 1
                
        except requests.exceptions.Timeout:
            print("FAIL (Timeout)")
            fail_count += 1
        except Exception as e:
            print(f"FAIL (Error: {str(e)})")
            fail_count += 1

    print("-" * 40)
    print(f"完了: 成功 {success_count} / 失敗 {fail_count} (合計 {total_files})")
    print(f"出力先: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
