"""ジョブの DB ステータス文字列から UI 用の進捗を導出する。"""


def progress_for_task_status(status: str) -> tuple[int, str]:
    """戻り値: (0–100 の進捗パーセント, 表示用キャプション)。"""
    if status == "processing":
        return 5, "処理を開始しています…"
    if status == "processing:reading_transcript":
        return 18, "文字起こし済みテキストを読み込み中…"
    if status == "processing:extracting_audio":
        return 10, "音声を取り出しています…"
    if status == "processing:transcribing":
        return 40, "文字起こし中（Whisper）…"
    if status.startswith("processing:extracting"):
        return 55, "内容を抽出しています…"
    if status == "processing:merging":
        return 80, "議事録にまとめています…"
    if status == "processing:summarizing":
        return 80, "要約・整形中…"
    if status == "processing:sending_notification":
        return 95, "通知を送っています…"
    return 0, "待機中…"
