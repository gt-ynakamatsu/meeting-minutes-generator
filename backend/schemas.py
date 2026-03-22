from typing import Literal, Optional

from pydantic import BaseModel, Field


class MeetingContext(BaseModel):
    purpose: str = ""
    participants: str = ""
    glossary: str = ""
    tone: str = ""
    action_rules: str = ""


class TaskSubmitMetadata(BaseModel):
    email: str = ""
    webhook_url: Optional[str] = None
    notification_type: Literal["browser", "webhook", "none"] = "browser"
    llm_provider: Literal["ollama", "openai"] = "ollama"
    ollama_model: str = "qwen2.5:7b"
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    topic: str = ""
    meeting_date: str = ""
    category: str = "未分類"
    tags: str = ""
    preset_id: str = "standard"
    context: MeetingContext = Field(default_factory=MeetingContext)


class SummaryPatch(BaseModel):
    summary: str


class RecordsQuery(BaseModel):
    days: int = 7
    search: str = ""
    category: str = ""
    status_filter: Literal["", "completed", "error", "processing"] = ""
