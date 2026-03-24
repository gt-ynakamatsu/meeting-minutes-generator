/** DB / worker の status 文字列から UI 用ラベルと進捗（推定）を算出 */

export type JobProgressKind = "pending" | "processing" | "completed" | "error" | "cancelled";

export type JobProgressInfo = {
  kind: JobProgressKind;
  title: string;
  detail?: string;
  /** 0–100 パイプライン全体のおおよその進捗 */
  overallPercent: number;
  /** 0–100 いまの工程内の進捗（チャンク処理時は i/n、それ以外は目安） */
  phasePercent: number;
};

const EXTRACT_RE = /^processing:extracting \((\d+)\/(\d+)\)\s*$/;

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

export function parseJobStatus(status: string): JobProgressInfo {
  const s = (status || "").trim();

  if (!s) {
    return { kind: "processing", title: "不明な状態", overallPercent: 0, phasePercent: 0 };
  }
  if (s === "pending") {
    return {
      kind: "pending",
      title: "キュー待ち",
      detail: "ワーカーがジョブを取りにいくまで待機中です",
      overallPercent: 3,
      phasePercent: 0,
    };
  }
  if (s.startsWith("Error")) {
    return {
      kind: "error",
      title: "エラー",
      detail: s.replace(/^Error:\s*/i, ""),
      overallPercent: 0,
      phasePercent: 0,
    };
  }
  if (s === "cancelled") {
    return {
      kind: "cancelled",
      title: "破棄済み",
      detail: "ユーザー操作により処理を中断しました",
      overallPercent: 0,
      phasePercent: 0,
    };
  }
  if (s === "completed") {
    return { kind: "completed", title: "完了", overallPercent: 100, phasePercent: 100 };
  }

  if (s === "processing:reading_transcript") {
    return {
      kind: "processing",
      title: "テキスト読み込み中",
      detail: "文字起こし済みファイルを読み込んでいます",
      overallPercent: 14,
      phasePercent: 55,
    };
  }
  if (s === "processing:extracting_audio") {
    return {
      kind: "processing",
      title: "音声抽出中",
      detail: "動画・音声から音声トラックを取り出しています",
      overallPercent: 11,
      phasePercent: 45,
    };
  }
  if (s === "processing:transcribing") {
    return {
      kind: "processing",
      title: "文字起こし中",
      detail: "Whisper（GPU）で音声認識しています",
      overallPercent: 28,
      phasePercent: 40,
    };
  }

  const m = s.match(EXTRACT_RE);
  if (m) {
    const i = parseInt(m[1], 10);
    const n = parseInt(m[2], 10);
    const nn = Math.max(n, 1);
    const ii = clamp(i, 0, nn);
    const phasePercent = Math.round((ii / nn) * 100);
    const overallPercent = Math.round(45 + (30 * ii) / nn);
    return {
      kind: "processing",
      title: "AI解析中（チャンク抽出）",
      detail: `チャンク ${ii} / ${nn} を LLM で構造化抽出しています`,
      overallPercent: clamp(overallPercent, 45, 74),
      phasePercent,
    };
  }

  if (s === "processing:merging") {
    return {
      kind: "processing",
      title: "議事録を統合中",
      detail: "抽出結果をマージし、最終の議事録にまとめています",
      overallPercent: 88,
      phasePercent: 55,
    };
  }

  if (s.startsWith("processing")) {
    return {
      kind: "processing",
      title: "処理中",
      detail: s,
      overallPercent: 50,
      phasePercent: 50,
    };
  }

  return { kind: "processing", title: s, overallPercent: 0, phasePercent: 0 };
}

export function jobStatusShortLabel(status: string): string {
  return parseJobStatus(status).title;
}
