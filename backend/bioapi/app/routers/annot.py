"""
注釈 API ルーター。

Ensembl REST / UniProt を用いて、遺伝子・タンパク質の基本情報を取得する。
"""

from fastapi import APIRouter, HTTPException

from ..models.schemas import (
    GeneAnnotResponse,
    ProteinAnnotResponse,
)
from ..services import annot_service


router = APIRouter(prefix="/annot", tags=["annot"])


@router.get("/gene/{gene_id}", response_model=GeneAnnotResponse)
async def get_gene_annot(gene_id: str) -> GeneAnnotResponse:
    """
    Ensembl Gene ID を指定して gene 注釈を取得する。
    """
    try:
        data = await annot_service.fetch_ensembl_gene(gene_id)
    except annot_service.AnnotFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return GeneAnnotResponse(**data)


@router.get("/protein/{accession}", response_model=ProteinAnnotResponse)
async def get_protein_annot(accession: str) -> ProteinAnnotResponse:
    """
    UniProt アクセッションを指定してタンパク質注釈を取得する。
    """
    try:
        data = await annot_service.fetch_uniprot_protein(accession)
    except annot_service.AnnotFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ProteinAnnotResponse(**data)

