"""
プライマー設計用の API ルーター。

エンドポイント:
- POST /primers/design
"""

import asyncio
from multiprocessing import cpu_count

from fastapi import APIRouter, HTTPException

from ..models.schemas import PrimerDesignRequest, PrimerDesignResponse, PrimerPair
from ..services import primer_service


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
            candidates_dicts = await asyncio.to_thread(
                primer_service.design_primers,
                sequence=request.sequence,
                num_return=request.num_return,
                product_size_range=request.product_size_range,
                target_start_1based=request.target_start,
                target_length=request.target_length,
                opt_tm=request.opt_tm,
                min_tm=request.min_tm,
                max_tm=request.max_tm,
                primer_min_size=request.primer_min_size,
                primer_opt_size=request.primer_opt_size,
                primer_max_size=request.primer_max_size,
                primer_min_gc=request.primer_min_gc,
                primer_max_gc=request.primer_max_gc,
                primer_salt_monovalent=request.primer_salt_monovalent,
                primer_dna_conc=request.primer_dna_conc,
            )
    except primer_service.Primer3NotFoundError as exc:
        # primer3_core が見つからない場合は 500 で詳細メッセージを返す
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except primer_service.Primer3ExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pairs = [PrimerPair(**c) for c in candidates_dicts]

    return PrimerDesignResponse(
        sequence_length=len("".join(request.sequence.split())),
        num_candidates=len(pairs),
        product_size_range=request.product_size_range,
        candidates=pairs,
    )
