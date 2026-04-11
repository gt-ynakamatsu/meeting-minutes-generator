"""管理者専用 API。"""

import sqlite3
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

import database as db
from backend.deps import AdminUser
from backend.http_utils import content_disposition_attachment
from backend.schemas import (
    AdminSuggestionBoxListResponse,
    AdminSuggestionBoxPatch,
    AdminCreateUserRequest,
    AdminPasswordResetRequest,
    AdminRolePatch,
    AdminUsageEventsResponse,
    AdminUsageSettingsSummaryResponse,
    AdminUsageSummaryResponse,
    AdminUserRow,
    UsageAdminNoteCreate,
    UsageAdminNoteRow,
    UsageCountPct,
    UsageEventRow,
    UsageMediaKindRow,
    UsageMetricsRollup,
    UsageModelBreakdownRow,
    UsagePresetBreakdownRow,
    SuggestionBoxRow,
)

router = APIRouter(tags=["admin"])

ERR_USER_NOT_FOUND = "ユーザーが見つかりません"
ERR_CANNOT_DELETE_SELF = "自分自身は削除できません"
ERR_EMAIL_ALREADY_USED = "このメールアドレスは既に使われています"
ERR_USER_CREATE_FAILED = "作成に失敗しました"
ERR_NOTE_SAVE_FAILED = "メモを保存できませんでした"
ERR_NOTE_FETCH_AFTER_CREATE_FAILED = "作成後の取得に失敗しました"
ERR_NOTE_NOT_FOUND = "メモが見つかりません"
ERR_SUGGESTION_NOT_FOUND = "目安箱チケットが見つかりません"
ERR_SUGGESTION_FETCH_FAILED = "更新後の目安箱チケット取得に失敗しました"
_EXPORT_EVENTS_PAGE_LIMIT = 500
_NOTIFICATION_TYPE_KEYS = ("browser", "webhook", "email", "none")
_NOTIFICATION_TYPE_LABELS = {
    "browser": "ブラウザ",
    "webhook": "Webhook",
    "email": "メール",
    "none": "なし",
}
_GUARD_EVENT_KEYS = ("rate_limited", "upload_too_large", "disk_low")
_GUARD_EVENT_LABELS = {
    "rate_limited": "レート制限",
    "upload_too_large": "サイズ上限超過",
    "disk_low": "空き容量不足",
}
_MEDIA_KIND_LABELS = {
    "video": "動画",
    "audio": "音声",
    "srt": "SRT",
    "txt": "テキスト",
    "other": "その他",
}


def _fmt_sec_hhmmss(s: float | None) -> str:
    if s is None:
        return "未計測"
    total = max(0, int(round(float(s))))
    h = total // 3600
    m = (total % 3600) // 60
    sec = total % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _fmt_usage_bytes(n: int | None) -> str:
    if n is None:
        return "未計測"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n / (1024 * 1024):.2f} MiB"


def _md_cell(v: object) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ").strip()


def _fmt_pct(v: object) -> str:
    try:
        return f"{float(v or 0):.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _notification_label(v: object) -> str:
    key = str(v or "").strip().lower()
    if not key:
        return "—"
    return _NOTIFICATION_TYPE_LABELS.get(key, key)


def _guard_event_label(v: object) -> str:
    key = str(v or "").strip().lower()
    if not key:
        return "—"
    return _GUARD_EVENT_LABELS.get(key, key)


def _media_kind_label(v: object) -> str:
    key = str(v or "").strip().lower()
    if not key:
        return "—"
    return _MEDIA_KIND_LABELS.get(key, key)


def _pipeline_label(ev: dict) -> str:
    return "書き起こしのみ" if bool(ev.get("transcript_only")) else "議事録まで"


def _supplementary_label(ev: dict) -> str:
    teams = ev.get("has_supplementary_teams") is True
    notes = ev.get("has_supplementary_notes") is True
    if teams and notes:
        return "Teams+メモ"
    if teams:
        return "Teams"
    if notes:
        return "メモ"
    if ev.get("has_supplementary_teams") is None and ev.get("has_supplementary_notes") is None:
        return "—"
    return "なし"


def _fetch_all_usage_events(days: int) -> tuple[list[dict], int]:
    items: list[dict] = []
    total = 0
    offset = 0
    while True:
        page, total = db.admin_usage_events(days, limit=_EXPORT_EVENTS_PAGE_LIMIT, offset=offset)
        if not page:
            break
        items.extend(page)
        offset += len(page)
        if offset >= total:
            break
    return items, total


def _processing_total_sec(ev: dict) -> float | None:
    parts = [
        ev.get("audio_extract_wall_sec"),
        ev.get("whisper_wall_sec"),
        ev.get("extract_llm_sec"),
        ev.get("merge_llm_sec"),
    ]
    total = 0.0
    measured = False
    for p in parts:
        if p is None:
            continue
        try:
            fv = float(p)
        except (TypeError, ValueError):
            continue
        total += max(0.0, fv)
        measured = True
    return total if measured else None


def _admin_user_row_model(r) -> AdminUserRow:
    return AdminUserRow(
        email=str(r["username"]),
        is_admin=bool(r["is_admin"]),
        created_at=str(r["created_at"]) if r.get("created_at") is not None else None,
    )


def _resolved_user_or_404(login_email: str) -> str:
    u = db.resolve_registry_username_for_mutation(login_email)
    if not u:
        raise HTTPException(status_code=404, detail=ERR_USER_NOT_FOUND)
    return u


def _suggestion_box_list_model(items: list[dict], total: int) -> AdminSuggestionBoxListResponse:
    return AdminSuggestionBoxListResponse(items=[SuggestionBoxRow(**x) for x in items], total=total)


@router.get("/api/admin/users", response_model=list[AdminUserRow])
def admin_list_users(_admin: AdminUser):
    rows = db.list_registry_users()
    return [_admin_user_row_model(r) for r in rows]


@router.post("/api/admin/users", response_model=AdminUserRow)
def admin_create_user(body: AdminCreateUserRequest, _admin: AdminUser):
    u = db.registry_login_normalize(body.email or "")
    pw = (body.password or "").replace("\r", "")
    try:
        db.create_registry_user(u, pw, is_admin=body.is_admin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=ERR_EMAIL_ALREADY_USED) from None
    row = db.get_user_by_username(u)
    if not row:
        raise HTTPException(status_code=500, detail=ERR_USER_CREATE_FAILED)
    return _admin_user_row_model(row)


@router.patch("/api/admin/users/{login_email}/password")
def admin_reset_password(login_email: str, body: AdminPasswordResetRequest, _admin: AdminUser):
    u = _resolved_user_or_404(login_email)
    try:
        ok = db.set_registry_user_password(u, (body.new_password or "").replace("\r", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail=ERR_USER_NOT_FOUND)
    return {"ok": True}


@router.patch("/api/admin/users/{login_email}/role")
def admin_set_role(login_email: str, body: AdminRolePatch, _admin: AdminUser):
    u = _resolved_user_or_404(login_email)
    try:
        db.set_registry_user_admin(u, body.is_admin)
    except KeyError:
        raise HTTPException(status_code=404, detail=ERR_USER_NOT_FOUND) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@router.delete("/api/admin/users/{login_email}")
def admin_delete_user(login_email: str, _admin: AdminUser):
    target = _resolved_user_or_404(login_email)
    admin_key = db.resolve_registry_username_for_mutation(_admin)
    if admin_key and admin_key == target:
        raise HTTPException(status_code=400, detail=ERR_CANNOT_DELETE_SELF)
    try:
        db.delete_registry_user(target)
    except KeyError:
        raise HTTPException(status_code=404, detail=ERR_USER_NOT_FOUND) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


def _usage_summary_model(data: dict) -> AdminUsageSummaryResponse:
    mr = data.get("metrics_rollup") or {}
    return AdminUsageSummaryResponse(
        period_days=int(data["period_days"]),
        total_submissions=int(data["total_submissions"]),
        pipeline_minutes_llm=UsageCountPct(**data["pipeline_minutes_llm"]),
        pipeline_transcript_only=UsageCountPct(**data["pipeline_transcript_only"]),
        provider_ollama=UsageCountPct(**data["provider_ollama"]),
        provider_openai=UsageCountPct(**data["provider_openai"]),
        ollama_models_for_llm_jobs=[UsageModelBreakdownRow(**x) for x in data["ollama_models_for_llm_jobs"]],
        openai_models_for_llm_jobs=[UsageModelBreakdownRow(**x) for x in data["openai_models_for_llm_jobs"]],
        whisper_presets_for_media=[UsagePresetBreakdownRow(**x) for x in data["whisper_presets_for_media"]],
        media_kind_breakdown=[UsageMediaKindRow(**x) for x in data["media_kind_breakdown"]],
        metrics_rollup=UsageMetricsRollup(**mr),
    )


def _usage_events_model(items: list[dict], total: int) -> AdminUsageEventsResponse:
    return AdminUsageEventsResponse(
        items=[UsageEventRow(**x) for x in items],
        total=total,
    )


def _usage_notes_model(rows: list[dict]) -> list[UsageAdminNoteRow]:
    return [UsageAdminNoteRow(**r) for r in rows]


@router.get("/api/admin/usage/summary", response_model=AdminUsageSummaryResponse)
def admin_usage_summary(_admin: AdminUser, days: int = Query(30, ge=1, le=365)):
    raw = db.admin_usage_summary(days)
    return _usage_summary_model(raw)


@router.get("/api/admin/usage/events", response_model=AdminUsageEventsResponse)
def admin_usage_events(
    _admin: AdminUser,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(80, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    items, total = db.admin_usage_events(days, limit=limit, offset=offset)
    return _usage_events_model(items, total)


@router.get("/api/admin/usage/settings-summary", response_model=AdminUsageSettingsSummaryResponse)
def admin_usage_settings_summary(_admin: AdminUser, days: int = Query(30, ge=1, le=365)):
    return AdminUsageSettingsSummaryResponse(**db.admin_usage_settings_summary(days))


@router.get("/api/admin/usage/export/md")
def admin_usage_export_md(_admin: AdminUser, days: int = Query(30, ge=1, le=365)):
    summary = db.admin_usage_summary(days)
    settings = db.admin_usage_settings_summary(days)
    notes = db.usage_admin_notes_list()
    events, total_events = _fetch_all_usage_events(days)

    lines = [
        "# 管理者ログエクスポート",
        "",
        f"- 出力日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 集計期間: 直近 {days} 日",
        f"- 総投入件数: {int(summary.get('total_submissions') or 0)} 件",
        "",
        "## サマリ",
        "",
        (
            f"- 議事録まで（LLM 推論あり）: {(summary.get('pipeline_minutes_llm') or {}).get('count', 0)} 件 "
            f"（{_fmt_pct((summary.get('pipeline_minutes_llm') or {}).get('pct'))}）"
        ),
        (
            f"- 書き起こしのみ: {(summary.get('pipeline_transcript_only') or {}).get('count', 0)} 件 "
            f"（{_fmt_pct((summary.get('pipeline_transcript_only') or {}).get('pct'))}）"
        ),
        (
            f"- LLM プロバイダ: Ollama {(summary.get('provider_ollama') or {}).get('count', 0)} 件 "
            f"（{_fmt_pct((summary.get('provider_ollama') or {}).get('pct'))}） / "
            f"OpenAI {(summary.get('provider_openai') or {}).get('count', 0)} 件 "
            f"（{_fmt_pct((summary.get('provider_openai') or {}).get('pct'))}）"
        ),
        "",
        "## 負荷・容量の目安（メトリクス記録済みの完了ジョブ）",
        "",
    ]
    mr = summary.get("metrics_rollup") or {}
    jobs_with_metrics = int(mr.get("jobs_with_metrics") or 0)
    if jobs_with_metrics > 0:
        lines.extend(
            [
                "|項目|合計|平均（1ジョブ）|",
                "|---|---:|---:|",
                f"|対象ジョブ数|{jobs_with_metrics} 件|—|",
                f"|入力ファイルサイズ|{_fmt_usage_bytes(mr.get('sum_input_bytes'))}|{_fmt_usage_bytes(mr.get('avg_input_bytes'))}|",
                f"|媒体の長さ（動画・音声の再生相当）|{_fmt_sec_hhmmss(mr.get('sum_media_duration_sec'))}|{_fmt_sec_hhmmss(mr.get('avg_media_duration_sec'))}|",
                f"|音声抽出（壁時計）|{_fmt_sec_hhmmss(mr.get('sum_audio_extract_sec'))}|{_fmt_sec_hhmmss(mr.get('avg_audio_extract_sec'))}|",
                f"|Whisper（壁時計）|{_fmt_sec_hhmmss(mr.get('sum_whisper_sec'))}|{_fmt_sec_hhmmss(mr.get('avg_whisper_sec'))}|",
                (
                    f"|書き起こし文字数|{int(mr.get('sum_transcript_chars') or 0):,} 字|"
                    f"{int(round(float(mr.get('avg_transcript_chars') or 0))):,} 字|"
                ),
                f"|議事録 LLM（チャンク抽出・壁時計）|{_fmt_sec_hhmmss(mr.get('sum_extract_llm_sec'))}|—|",
                f"|議事録 LLM（結合・壁時計）|{_fmt_sec_hhmmss(mr.get('sum_merge_llm_sec'))}|—|",
                f"|議事録 LLM 合計（抽出＋結合）|{_fmt_sec_hhmmss(mr.get('sum_llm_sec'))}|—|",
                f"|LLM チャンク処理回数（合計）|{int(mr.get('sum_llm_chunks') or 0):,} 回|—|",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "この期間にメトリクスが揃った完了ジョブはまだありません（新規データはワーカー更新後から蓄積されます）。",
                "",
            ]
        )

    ollama_rows = summary.get("ollama_models_for_llm_jobs") or []
    if ollama_rows:
        lines.extend(["## Ollama モデル（議事録生成ジョブのみ）", "", "|モデル|件数|割合|", "|---|---:|---:|"])
        for r in ollama_rows:
            lines.append(f"|{_md_cell(r.get('model') or '—')}|{int(r.get('count') or 0)}|{_fmt_pct(r.get('pct'))}|")
        lines.append("")

    openai_rows = summary.get("openai_models_for_llm_jobs") or []
    if openai_rows:
        lines.extend(["## OpenAI モデル（議事録生成ジョブのみ）", "", "|モデル|件数|割合|", "|---|---:|---:|"])
        for r in openai_rows:
            lines.append(f"|{_md_cell(r.get('model') or '—')}|{int(r.get('count') or 0)}|{_fmt_pct(r.get('pct'))}|")
        lines.append("")

    whisper_rows = summary.get("whisper_presets_for_media") or []
    if whisper_rows:
        lines.extend(["## Whisper 品質プリセット（動画・音声ジョブのみ）", "", "|プリセット|件数|割合|", "|---|---:|---:|"])
        for r in whisper_rows:
            lines.append(f"|{_md_cell(r.get('preset') or '—')}|{int(r.get('count') or 0)}|{_fmt_pct(r.get('pct'))}|")
        lines.append("")

    media_rows = summary.get("media_kind_breakdown") or []
    if media_rows:
        lines.extend(["## 投入ファイルの種別（拡張子から推定）", "", "|種別|件数|割合|", "|---|---:|---:|"])
        for r in media_rows:
            lines.append(f"|{_media_kind_label(r.get('kind'))}|{int(r.get('count') or 0)}|{_fmt_pct(r.get('pct'))}|")
        lines.append("")

    lines.extend(
        [
            "## 設定利用の内訳（全部）",
            "",
            "### 通知方式",
            "",
            "|方式|件数|割合|",
            "|---|---:|---:|",
        ]
    )
    notif_map = {str(r.get("value") or ""): r for r in (settings.get("notification_breakdown") or [])}
    for key in _NOTIFICATION_TYPE_KEYS:
        r = notif_map.get(key) or {}
        lines.append(f"|{_notification_label(key)}|{int(r.get('count') or 0)}|{_fmt_pct(r.get('pct'))}|")

    lines.extend(
        [
            "",
            "### 参考資料利用",
            "",
            "|項目|件数|割合|",
            "|---|---:|---:|",
            (
                f"|Teams トランスクリプトあり|{(settings.get('supplementary_teams_used') or {}).get('count', 0)}|"
                f"{_fmt_pct((settings.get('supplementary_teams_used') or {}).get('pct'))}|"
            ),
            (
                f"|担当メモあり|{(settings.get('supplementary_notes_used') or {}).get('count', 0)}|"
                f"{_fmt_pct((settings.get('supplementary_notes_used') or {}).get('pct'))}|"
            ),
            (
                f"|参考資料いずれかあり|{(settings.get('supplementary_any_used') or {}).get('count', 0)}|"
                f"{_fmt_pct((settings.get('supplementary_any_used') or {}).get('pct'))}|"
            ),
            "",
            "### 防御イベント",
            "",
            "|イベント|件数|割合|",
            "|---|---:|---:|",
        ]
    )
    guard_map = {str(r.get("event_type") or ""): int(r.get("count") or 0) for r in (settings.get("guard_events") or [])}
    total_guard = int(settings.get("total_guard_events") or 0)
    for key in _GUARD_EVENT_KEYS:
        count = guard_map.get(key, 0)
        pct = (count * 100.0 / total_guard) if total_guard > 0 else 0.0
        lines.append(f"|{_guard_event_label(key)}|{count}|{pct:.1f}%|")

    lines.extend(
        [
            "",
            f"## 直近の投入イベント（{len(events)} / {total_events} 件）",
            "",
            "|日時|タスクID|ユーザー|パイプライン|LLM|モデル|媒体|Whisper|通知|参考資料|入力|収録時間|抽出|Whisper時|文字|LLM抽出|LLM結合|チャンク|完了まで|処理合計|",
            "|---|---|---|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for ev in events:
        lines.append(
            "|".join(
                [
                    "",
                    _md_cell(ev.get("created_at") or ""),
                    _md_cell(ev.get("task_id") or ""),
                    _md_cell(ev.get("user_email") or ""),
                    _pipeline_label(ev),
                    _md_cell(ev.get("llm_provider") or ""),
                    _md_cell(ev.get("model_name") or ""),
                    _media_kind_label(ev.get("media_kind") or ""),
                    _md_cell(ev.get("whisper_preset") or ""),
                    _notification_label(ev.get("notification_type")),
                    _supplementary_label(ev),
                    _fmt_usage_bytes(ev.get("input_bytes")),
                    _fmt_sec_hhmmss(ev.get("media_duration_sec")),
                    _fmt_sec_hhmmss(ev.get("audio_extract_wall_sec")),
                    _fmt_sec_hhmmss(ev.get("whisper_wall_sec")),
                    _md_cell(ev.get("transcript_chars") if ev.get("transcript_chars") is not None else "未計測"),
                    _fmt_sec_hhmmss(ev.get("extract_llm_sec")),
                    _fmt_sec_hhmmss(ev.get("merge_llm_sec")),
                    _md_cell(ev.get("llm_chunks") if ev.get("llm_chunks") is not None else "未計測"),
                    _fmt_sec_hhmmss(ev.get("completion_wall_sec")),
                    _fmt_sec_hhmmss(_processing_total_sec(ev)),
                    "",
                ]
            )
        )

    lines.extend(["", "## 運用メモ（経営・サーバ強化の根拠など）", ""])
    if notes:
        for n in notes:
            lines.extend(
                [
                    f"### {_md_cell(n.get('created_at') or '')} / {_md_cell(n.get('author_email') or '')}",
                    "",
                    str(n.get("body") or "").strip() or "(空欄)",
                    "",
                ]
            )
    else:
        lines.append("- メモはありません。")

    body = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
    fn = f"admin_usage_{days}d_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": content_disposition_attachment(fn)},
    )


@router.get("/api/admin/usage/notes", response_model=list[UsageAdminNoteRow])
def admin_usage_notes_list(_admin: AdminUser):
    rows = db.usage_admin_notes_list()
    return _usage_notes_model(rows)


@router.post("/api/admin/usage/notes", response_model=UsageAdminNoteRow)
def admin_usage_notes_add(body: UsageAdminNoteCreate, admin: AdminUser):
    nid = db.usage_admin_note_add(admin, body.body)
    if nid is None:
        raise HTTPException(status_code=400, detail=ERR_NOTE_SAVE_FAILED)
    row = db.usage_admin_note_get(nid)
    if not row:
        raise HTTPException(status_code=500, detail=ERR_NOTE_FETCH_AFTER_CREATE_FAILED)
    return UsageAdminNoteRow(**row)


@router.delete("/api/admin/usage/notes/{note_id}")
def admin_usage_notes_delete(note_id: int, _admin: AdminUser):
    if not db.usage_admin_note_delete(note_id):
        raise HTTPException(status_code=404, detail=ERR_NOTE_NOT_FOUND)
    return {"ok": True}


@router.get("/api/admin/suggestion-box", response_model=AdminSuggestionBoxListResponse)
def admin_suggestion_box_list(
    _admin: AdminUser,
    status: str = Query("", pattern="^(|new|in_progress|done)$"),
    limit: int = Query(80, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    items, total = db.suggestion_box_admin_list(status=status, limit=limit, offset=offset)
    return _suggestion_box_list_model(items, total)


@router.patch("/api/admin/suggestion-box/{ticket_id}", response_model=SuggestionBoxRow)
def admin_suggestion_box_patch(ticket_id: int, body: AdminSuggestionBoxPatch, _admin: AdminUser):
    if not db.suggestion_box_admin_update(ticket_id, status=body.status, admin_note=body.admin_note):
        raise HTTPException(status_code=404, detail=ERR_SUGGESTION_NOT_FOUND)
    row = db.suggestion_box_admin_get(ticket_id)
    if not row:
        raise HTTPException(status_code=404, detail=ERR_SUGGESTION_FETCH_FAILED)
    return SuggestionBoxRow(**row)
