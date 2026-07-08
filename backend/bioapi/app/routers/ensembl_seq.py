"""
Ensembl REST API を用いてゲノム領域の配列を取得するためのルーター。

MVP では、species / chr / start / end / strand を指定して
DNA 配列を取得する簡単な GET エンドポイントだけを提供する。
"""

from fastapi import APIRouter, HTTPException, Query

import httpx

from ..core.config import get_settings


router = APIRouter(prefix="/ensembl", tags=["ensembl"])


@router.get("/sequence/region")
async def get_region_sequence(
    species: str = Query(..., description="Ensembl species 名（例: homo_sapiens, arabidopsis_thaliana）"),
    chr: str = Query(..., alias="chr", description="染色体 / コンティグ名（例: 4LG4, 13 など）"),
    start: int = Query(..., ge=1, description="開始座標（1-based）"),
    end: int = Query(..., ge=1, description="終了座標（1-based）"),
    strand: int = Query(1, description="ストランド（1 または -1）"),
):
    """
    Ensembl REST の /sequence/region をラップし、指定領域の DNA 配列を返す。
    """
    if end < start:
        raise HTTPException(
            status_code=400,
            detail="end は start 以上である必要があります。",
        )

    region_str = f"{chr}:{start}..{end}:{strand}"
    settings = get_settings()
    base = settings.ensembl_rest_base_url.rstrip("/")
    url = f"{base}/sequence/region/{species}/{region_str}"

    params = {"content-type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"Accept": "application/json"},
            )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ensembl REST へのリクエストに失敗しました: {exc}",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Ensembl REST から配列を取得できませんでした: {resp.status_code} {resp.text}",
        )

    try:
        data = resp.json()
    except Exception:
        raise HTTPException(
            status_code=502,
            detail=f"Ensembl REST 応答の解析に失敗しました: {resp.text[:200]}",
        )

    seq = data.get("seq")
    if not isinstance(seq, str) or not seq:
        raise HTTPException(
            status_code=502,
            detail="Ensembl REST 応答に seq フィールドが含まれていません。",
        )

    return {
        "species": species,
        "chr": chr,
        "start": start,
        "end": end,
        "strand": strand,
        "length": len(seq),
        "seq": seq,
        "source": base,
    }


