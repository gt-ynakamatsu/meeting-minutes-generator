import json
import os
import sys

# --- 設定 ---
# 入力ファイルパス
INPUT_FILE = os.path.join("input", "whisper_result.json")
# 出力ディレクトリ
OUTPUT_DIR = os.path.join("work", "chunks")
# 1チャンクあたりの時間（秒）
CHUNK_SEC = 75

def format_timestamp(seconds):
    """秒数を HH:MM:SS 形式に変換"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def main():
    # 1. 入力チェック
    if not os.path.exists(INPUT_FILE):
        print(f"Error: 入力ファイルが見つかりません -> {INPUT_FILE}")
        sys.exit(1)

    # 2. JSON読み込み
    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        print(f"Error: JSONの読み込みに失敗しました -> {INPUT_FILE}")
        sys.exit(1)

    # chunks/segments どちらも許容する（Whisperのバージョン等による差異吸収）
    segments = data.get("segments") or data.get("chunks")
    
    if segments is None:
        print("Error: JSON内に 'segments' または 'chunks' 配列が見つかりません。")
        sys.exit(1)
        
    if not isinstance(segments, list):
        print("Error: 'segments' がリスト形式ではありません。")
        sys.exit(1)

    # 3. 出力ディレクトリ作成
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 4. チャンク分割処理
    current_chunk_lines = []
    chunk_start_time = None
    file_index = 1
    
    saved_count = 0

    print(f"Processing {len(segments)} segments...")

    for i, seg in enumerate(segments):
        start = seg.get("start")
        end = seg.get("end")
        text = seg.get("text", "")

        # 必須フィールドのチェック
        if start is None or end is None:
            continue

        # 最初のセグメントの開始時間を記録
        if chunk_start_time is None:
            chunk_start_time = start

        # 行フォーマット: [HH:MM:SS-HH:MM:SS] テキスト
        ts_str = f"[{format_timestamp(start)}-{format_timestamp(end)}]"
        line = f"{ts_str} {text}"
        current_chunk_lines.append(line)

        # チャンク区切り判定 (現在のセグメントの終了時間 - チャンク開始時間 >= 設定秒数)
        # ※ 文脈が切れないように、本来は句点などで判断するのが望ましいが、今回は単純な時間分割
        if (end - chunk_start_time) >= CHUNK_SEC:
            # ファイル書き出し
            out_filename = f"chunk_{file_index:03d}.txt"
            out_path = os.path.join(OUTPUT_DIR, out_filename)
            
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(current_chunk_lines))
            
            # リセット
            current_chunk_lines = []
            chunk_start_time = None
            file_index += 1
            saved_count += 1

    # 残りの分を保存
    if current_chunk_lines:
        out_filename = f"chunk_{file_index:03d}.txt"
        out_path = os.path.join(OUTPUT_DIR, out_filename)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(current_chunk_lines))
        saved_count += 1

    print(f"完了: 合計 {saved_count} 個のチャンクファイルを作成しました。")
    print(f"出力先: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
