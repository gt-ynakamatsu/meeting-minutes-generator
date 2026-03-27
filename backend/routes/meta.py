"""ヘルス・版情報・Ollama タグ一覧。"""

from fastapi import APIRouter

from backend.ollama_client import fetch_ollama_model_names
from backend.schemas import OllamaModelsResponse

router = APIRouter(tags=["meta"])


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.get("/api/ollama/models", response_model=OllamaModelsResponse)
def ollama_models():
    """ブラウザが Ollama に直アクセスできないため、API 経由で /api/tags を返す。

    モデル名は機密ではないため認証は不要（認証 ON かつ未ログインでもフォーム表示前に候補を取れるようにする）。
    """
    return OllamaModelsResponse(models=fetch_ollama_model_names())


@router.get("/api/version")
def api_version():
    try:
        from version import __version__

        return {"version": __version__}
    except Exception:
        return {"version": "unknown"}
