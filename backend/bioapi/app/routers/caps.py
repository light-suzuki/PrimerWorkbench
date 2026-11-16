"""
CAPS（制限酵素で共優勢判定できる）マーカー設計 API。

エンドポイント:
- POST /caps/design
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from ..models.schemas import CapsDesignRequest, CapsDesignResponse, JobCreateResponse
from ..services import blast_service
from ..application import caps_design, caps_payload
from ..services import job_service


router = APIRouter(prefix="/caps", tags=["caps"])


@router.post("/design", response_model=CapsDesignResponse)
async def design_caps(request: CapsDesignRequest) -> CapsDesignResponse:
    """
    CAPS 候補をまとめて生成する。
    """
    try:
        return await asyncio.to_thread(caps_design, request)
    except (ValueError, blast_service.BlastExecutionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@router.post("/design_job", response_model=JobCreateResponse)
async def design_caps_job(request: CapsDesignRequest) -> JobCreateResponse:
    """
    CAPS 生成をバックグラウンドジョブとして実行する。
    """

    def work(job: job_service.Job):
        return caps_payload(request,
            progress_cb=lambda p, m=None: job.update(progress=p, message=m),
            cancel_cb=job.is_cancel_requested)

    try:
        job = job_service.submit_job("caps_design", work)
    except job_service.JobQueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        # submit 自体は軽い想定だが、念のため
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JobCreateResponse(job_id=job.id)

