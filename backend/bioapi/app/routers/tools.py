"""
ツールバージョン情報 API。

エンドポイント:
- GET /tools/versions
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..services import blast_service, primer_service


router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("/versions")
async def tool_versions() -> dict[str, str]:
    """
    レポート再現用に、解析で使うツールのバージョンを返す。

    tool が未取得の場合は "unavailable" を返す。
    """
    return {
        "app_version": __version__,
        "primer3_version": primer_service.get_primer3_version(),
        "blast_version": blast_service.get_blast_version(),
    }
