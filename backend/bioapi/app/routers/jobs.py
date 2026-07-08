"""
ジョブ（重い処理）の状態確認 API。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from ..models.schemas import JobCreateResponse, JobInfo
from ..services import job_service


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobInfo])
async def list_jobs(
    kind: str | None = Query(None, description="ジョブ種別(kind)で絞り込み"),
    status: Literal["queued", "running", "succeeded", "failed", "canceled"] | None = Query(
        None,
        description="ジョブ状態で絞り込み",
    ),
    limit: int = Query(50, ge=1, le=200, description="返す件数上限"),
) -> list[JobInfo]:
    jobs = job_service.list_jobs(kind=kind, status=status, limit=limit)
    return [JobInfo(**j.to_public_dict()) for j in jobs]


@router.get("/{job_id}", response_model=JobInfo)
async def get_job(job_id: str) -> JobInfo:
    job = job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job が見つかりません。")
    return JobInfo(**job.to_public_dict())


@router.get("/{job_id}/result")
async def get_job_result(job_id: str):
    job = job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job が見つかりません。")
    info = job.to_public_dict()
    if info["status"] == "succeeded":
        return job.result
    if info["status"] == "canceled":
        raise HTTPException(status_code=409, detail=info.get("error") or "キャンセルされました。")
    if info["status"] == "failed":
        raise HTTPException(status_code=500, detail=info.get("error") or "ジョブが失敗しました。")
    raise HTTPException(status_code=425, detail="ジョブはまだ完了していません。")


@router.post("/{job_id}/cancel", response_model=JobInfo)
async def cancel_job(job_id: str) -> JobInfo:
    job = job_service.cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job が見つかりません。")
    return JobInfo(**job.to_public_dict())


@router.post("/noop", response_model=JobCreateResponse)
async def noop_job() -> JobCreateResponse:
    """
    開発用: ジョブ基盤の疎通確認。
    """

    def work(job: job_service.Job):
        job.update(progress=0.5, message="working")
        return {"ok": True}

    try:
        job = job_service.submit_job("noop", work)
    except job_service.JobQueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return JobCreateResponse(job_id=job.id)
