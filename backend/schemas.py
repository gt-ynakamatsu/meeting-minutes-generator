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
    notification_type: Literal["browser", "webhook", "email", "none"] = "browser"
    llm_provider: Literal["ollama", "openai"] = "ollama"
    ollama_model: str = "qwen2.5:7b"
    # 認証オフ時のみ使用。ログイン時はサーバに保存したキーを使います。
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


class LoginRequest(BaseModel):
    email: str = ""
    password: str = ""


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthStatusResponse(BaseModel):
    auth_required: bool
    bootstrap_needed: bool
    self_register_allowed: bool = True
    # MM_SMTP_HOST / MM_SMTP_FROM が揃っているとき True（メール通知が利用可能）
    email_notify_available: bool = False
    # MM_OPENAI_ENABLED がオフのとき False（フロントで OpenAI UI を隠す）
    openai_enabled: bool = True


class BootstrapRequest(BaseModel):
    email: str = ""
    password: str = ""


class AuthMeResponse(BaseModel):
    email: str
    is_admin: bool


class AdminUserRow(BaseModel):
    email: str
    is_admin: bool
    created_at: Optional[str] = None


class AdminCreateUserRequest(BaseModel):
    email: str = ""
    password: str = ""
    is_admin: bool = False


class AdminPasswordResetRequest(BaseModel):
    new_password: str = ""


class AdminRolePatch(BaseModel):
    is_admin: bool


class MeLLMResponse(BaseModel):
    openai_configured: bool
    openai_model: str
    openai_feature_enabled: bool = True


class MeLLMPatch(BaseModel):
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None


class OllamaModelsResponse(BaseModel):
    """GET /api/ollama/models — Ollama /api/tags の name 一覧"""

    models: list[str] = []


class RecordsQuery(BaseModel):
    days: int = 7
    search: str = ""
    category: str = ""
    status_filter: Literal["", "completed", "error", "processing"] = ""
