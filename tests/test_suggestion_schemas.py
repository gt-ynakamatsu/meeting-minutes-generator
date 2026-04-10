import pytest
from pydantic import ValidationError

from backend.schemas import AdminSuggestionBoxPatch, MeetingContext, TaskSubmitMetadata


def test_admin_suggestion_patch_requires_any_field():
    with pytest.raises(ValidationError):
        AdminSuggestionBoxPatch()


def test_admin_suggestion_patch_accepts_status_only():
    m = AdminSuggestionBoxPatch(status="new")
    assert m.status == "new"
    assert m.admin_note is None


def test_admin_suggestion_patch_accepts_note_only():
    m = AdminSuggestionBoxPatch(admin_note="memo")
    assert m.status is None
    assert m.admin_note == "memo"


def test_task_submit_metadata_legacy_audio_extract_only_sets_transcript_only():
    m = TaskSubmitMetadata(
        email="",
        webhook_url=None,
        notification_type="browser",
        llm_provider="ollama",
        ollama_model="qwen2.5:7b",
        openai_api_key=None,
        openai_model="gpt-4o-mini",
        topic="",
        meeting_date="",
        category="未分類",
        tags="",
        preset_id="standard",
        context=MeetingContext(),
        whisper_preset="accurate",
        transcript_only=False,
        audio_extract_only=True,
    )
    assert m.transcript_only is True
