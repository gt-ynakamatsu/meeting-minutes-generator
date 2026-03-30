from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


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
    # faster-whisper のビーム探索など。動画・音声の文字起こし時のみワーカーで解釈
    whisper_preset: Literal["fast", "balanced", "accurate"] = "balanced"
    # True のとき書き起こしまで（.txt/.srt 読込 or Whisper）で完了し、議事録用 LLM は実行しない
    transcript_only: bool = False
    # 旧 API 名（誤解を招くが互換のため残す）。True なら transcript_only と同義
    audio_extract_only: bool = False

    @model_validator(mode="after")
    def _merge_transcript_only_legacy(self) -> "TaskSubmitMetadata":
        if self.audio_extract_only:
            self.transcript_only = True
        return self


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
    # MM_EMAIL_NOTIFY_ENABLED がオンのとき True（メール通知を UI に出す・API で受け付ける）
    email_notify_feature_enabled: bool = False
    # 上記がオンかつ MM_SMTP_* が揃っているとき True（実際にメールを送れる）
    email_notify_available: bool = False
    # MM_OPENAI_ENABLED がオフのとき False（フロントで OpenAI UI を隠す）
    openai_enabled: bool = True
    # SMTP 設定済みかつ宛先あり（管理者メール or MM_ERROR_REPORT_TO）のとき True
    error_report_available: bool = False


class ErrorReportRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    detail: str = Field(default="", max_length=12000)
    page_url: str = Field(default="", max_length=2000)
    client_version: str = Field(default="", max_length=64)


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
