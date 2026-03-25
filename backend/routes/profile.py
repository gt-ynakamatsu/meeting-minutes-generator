"""ログインユーザーのプロファイル（OpenAI 設定など）。"""

from fastapi import APIRouter, HTTPException

import database as db
import feature_flags
from backend.auth_settings import auth_enabled
from backend.deps import ApiUser
from backend.schemas import MeLLMPatch, MeLLMResponse

router = APIRouter(tags=["profile"])


@router.get("/api/me/llm", response_model=MeLLMResponse)
def me_llm_get(_auth: ApiUser):
    oa = feature_flags.openai_feature_enabled()
    if not auth_enabled():
        return MeLLMResponse(
            openai_configured=False,
            openai_model="gpt-4o-mini",
            openai_feature_enabled=oa,
        )
    if not (_auth or "").strip():
        raise HTTPException(status_code=401, detail="認証が必要です")
    key, model = db.get_user_openai_settings(_auth)
    return MeLLMResponse(
        openai_configured=bool(key) if oa else False,
        openai_model=model,
        openai_feature_enabled=oa,
    )


@router.patch("/api/me/llm")
def me_llm_patch(body: MeLLMPatch, _auth: ApiUser):
    if not feature_flags.openai_feature_enabled():
        raise HTTPException(
            status_code=400,
            detail="OpenAI 連携は無効です（MM_OPENAI_ENABLED）。有効にするには環境変数を設定して API を再起動してください。",
        )
    if not auth_enabled():
        raise HTTPException(status_code=400, detail="認証が無効なためサーバに保存できません")
    if not (_auth or "").strip():
        raise HTTPException(status_code=401, detail="認証が必要です")
    api_key_arg = None
    if "openai_api_key" in body.model_fields_set:
        api_key_arg = (body.openai_api_key or "").strip()
    model_arg = None
    if "openai_model" in body.model_fields_set:
        model_arg = (body.openai_model or "").strip() or "gpt-4o-mini"
    if api_key_arg is None and model_arg is None:
        return {"ok": True}
    db.update_user_openai(_auth, api_key=api_key_arg, model=model_arg)
    return {"ok": True}
