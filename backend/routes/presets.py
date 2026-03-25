"""プリセット JSON の配信。"""

from fastapi import APIRouter

from backend.deps import ApiUser
from backend.presets_io import load_presets_dict

router = APIRouter(tags=["presets"])


@router.get("/api/presets")
def get_presets(_auth: ApiUser):
    return load_presets_dict()
