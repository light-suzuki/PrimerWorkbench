"""
シーケンス解析用の API ルーター。

エンドポイント:
- POST /sequence/analyze/basic
- POST /sequence/analyze/orfs
- POST /sequence/analyze/restriction
"""

from fastapi import APIRouter, HTTPException

from ..models.schemas import (
    OrfAnalysisRequest,
    OrfAnalysisResponse,
    RestrictionAnalysisRequest,
    RestrictionAnalysisResponse,
    RestrictionCutSite,
    SequenceBasicAnalysisRequest,
    SequenceBasicAnalysisResponse,
)
from ..services import sequence_service


router = APIRouter(prefix="/sequence", tags=["sequence"])


@router.post("/analyze/basic", response_model=SequenceBasicAnalysisResponse)
async def analyze_basic(request: SequenceBasicAnalysisRequest) -> SequenceBasicAnalysisResponse:
    """
    basic シーケンス解析エンドポイント。

    - 長さ
    - GC%
    - オプションで 3 フレームの翻訳結果
    """
    try:
        result_dict = sequence_service.analyze_basic(
            sequence=request.sequence,
            include_translation=request.include_translation,
        )
    except ValueError as exc:
        # ユーザー入力に起因するエラーは 400 Bad Request として返す
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SequenceBasicAnalysisResponse(**result_dict)


@router.post("/analyze/orfs", response_model=OrfAnalysisResponse)
async def analyze_orfs(request: OrfAnalysisRequest) -> OrfAnalysisResponse:
    """
    ORF 検出エンドポイント。

    - 開始コドン ATG
    - 終止コドン TAA/TAG/TGA
    - min_aa_length 以上の ORF のみ返す
    """
    try:
        orfs = sequence_service.find_orfs(
            sequence=request.sequence,
            min_aa_length=request.min_aa_length,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return OrfAnalysisResponse(orfs=orfs)


@router.post("/analyze/restriction", response_model=RestrictionAnalysisResponse)
async def analyze_restriction(
    request: RestrictionAnalysisRequest,
) -> RestrictionAnalysisResponse:
    """
    制限酵素サイト解析エンドポイント。

    指定された酵素名ごとに、切断位置（1-based）一覧を返す。
    """
    if not request.enzymes:
        raise HTTPException(
            status_code=400,
            detail="少なくとも 1 つ以上の制限酵素名を指定してください。",
        )

    result = sequence_service.analyze_restriction_sites(
        sequence=request.sequence,
        enzymes=request.enzymes,
    )

    cut_site_models = [
        RestrictionCutSite(enzyme=name, cut_positions=positions)
        for name, positions in result.items()
    ]

    return RestrictionAnalysisResponse(
        sequence_length=len("".join(request.sequence.split())),
        results=cut_site_models,
    )

