"""
プライマー設計用の API ルーター。

エンドポイント:
- POST /primers/design
"""

import asyncio
from multiprocessing import cpu_count

from fastapi import APIRouter, HTTPException

from ..models.schemas import PrimerDesignRequest, PrimerDesignResponse
from ..services import primer_service
from ..application import primers_design


router = APIRouter(prefix="/primers", tags=["primers"])

# primer3_core は 1 回の実行が CPU を強く使うため、同時実行数を適度に制限する。
# 目安:
# - 24 threads (12 cores) → 12 並列
# - 8 threads → 4 並列
_PRIMER3_MAX_CONCURRENCY = max(1, min(12, (cpu_count() or 2) // 2))
_PRIMER3_SEM = asyncio.Semaphore(_PRIMER3_MAX_CONCURRENCY)


@router.post("/design", response_model=PrimerDesignResponse)
async def design_primers(request: PrimerDesignRequest) -> PrimerDesignResponse:
    """
    Primer3（primer3_core）をラップしたプライマー設計エンドポイント。
    """
    try:
        async with _PRIMER3_SEM:
            return await asyncio.to_thread(primers_design, request)
    except primer_service.Primer3NotFoundError as exc:
        # primer3_core が見つからない場合は 500 で詳細メッセージを返す
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except primer_service.Primer3ExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
