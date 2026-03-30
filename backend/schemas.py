from typing import Any, Literal, Optional

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
    # MM_MINUTES_RETENTION_DAYS（未設定時 30≒1か月）。0 以下のとき自動削除は行わない
    minutes_retention_days: int


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


class RecordsPageResponse(BaseModel):
    """GET /api/records … 一覧は items、フィルタ一致の総件数は total（ページング用）。"""

    items: list[dict[str, Any]]
    total: int


class UsageCountPct(BaseModel):
    count: int = 0
    pct: float = 0.0


class UsageModelBreakdownRow(BaseModel):
    model: str
    count: int
    pct: float


class UsagePresetBreakdownRow(BaseModel):
    preset: str
    count: int
    pct: float


class UsageMediaKindRow(BaseModel):
    kind: str
    count: int
    pct: float


class AdminUsageSummaryResponse(BaseModel):
    period_days: int
    total_submissions: int
    pipeline_minutes_llm: UsageCountPct
    pipeline_transcript_only: UsageCountPct
    provider_ollama: UsageCountPct
    provider_openai: UsageCountPct
    ollama_models_for_llm_jobs: list[UsageModelBreakdownRow]
    openai_models_for_llm_jobs: list[UsageModelBreakdownRow]
    whisper_presets_for_media: list[UsagePresetBreakdownRow]
    media_kind_breakdown: list[UsageMediaKindRow]


class UsageEventRow(BaseModel):
    id: int
    created_at: str
    task_id: str
    user_email: str
    transcript_only: bool
    llm_provider: str
    model_name: str
    whisper_preset: str
    media_kind: str


class AdminUsageEventsResponse(BaseModel):
    items: list[UsageEventRow]
    total: int


class UsageAdminNoteRow(BaseModel):
    id: int
    created_at: str
    author_email: str
    body: str


class UsageAdminNoteCreate(BaseModel):
    body: str = Field(default="", min_length=1, max_length=8000)
