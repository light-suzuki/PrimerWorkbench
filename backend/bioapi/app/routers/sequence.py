"""HTTP adapters for the shared sequence application operations."""
from fastapi import APIRouter, HTTPException
from ..application import sequence_basic, sequence_orfs, sequence_restriction
from ..models.schemas import (
    SequenceBasicAnalysisRequest, SequenceBasicAnalysisResponse,
    OrfAnalysisRequest, OrfAnalysisResponse, RestrictionAnalysisRequest, RestrictionAnalysisResponse,
)
from ..services.enzyme_catalog import catalog

router = APIRouter(prefix="/sequence", tags=["sequence"])


@router.get("/enzymes")
async def enzyme_catalog():
    return catalog()


@router.post("/analyze/basic", response_model=SequenceBasicAnalysisResponse)
async def analyze_basic(request: SequenceBasicAnalysisRequest):
    try:
        return sequence_basic(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/analyze/orfs", response_model=OrfAnalysisResponse)
async def analyze_orfs(request: OrfAnalysisRequest):
    try:
        return sequence_orfs(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/analyze/restriction", response_model=RestrictionAnalysisResponse)
async def analyze_restriction(request: RestrictionAnalysisRequest):
    try:
        return sequence_restriction(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

