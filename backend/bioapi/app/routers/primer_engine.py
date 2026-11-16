"""Workbench job adapter for the embedded PrimerBLAST engine."""
from fastapi import APIRouter, HTTPException
from ..services import job_service
from ..services.primer_engine import PrimerEngineRequest, design
from ..models.schemas import JobCreateResponse

router = APIRouter(prefix="/primers", tags=["primers"])


@router.post("/screen_job", response_model=JobCreateResponse)
def screen_job(request: PrimerEngineRequest):
    def work(job):
        job.raise_if_cancel_requested()
        job.update(progress=0.05, message="PrimerBLAST OSS: design and specificity")
        result = design(request)
        job.raise_if_cancel_requested()
        return result
    try:
        job = job_service.submit_job("primer_screen", work)
    except job_service.JobQueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return JobCreateResponse(job_id=job.id)
