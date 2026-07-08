"""
CAPS（制限酵素で共優勢判定できる）マーカー設計 API。

エンドポイント:
- POST /caps/design
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from ..models.schemas import CapsDesignRequest, CapsDesignResponse, CapsMarkerRow, JobCreateResponse
from ..services import blast_service
from ..services import caps_service
from ..services import job_service


router = APIRouter(prefix="/caps", tags=["caps"])


@router.post("/design", response_model=CapsDesignResponse)
async def design_caps(request: CapsDesignRequest) -> CapsDesignResponse:
    """
    CAPS 候補をまとめて生成する。
    """
    try:
        payload = await asyncio.to_thread(
            caps_service.design_caps_markers,
            ref_db=request.ref_db,
            ref_entry=request.ref_entry,
            ref_start=request.ref_start,
            ref_end=request.ref_end,
            alt_db=request.alt_db,
            map_alt_by_blast=request.map_alt_by_blast,
            alt_entry=request.alt_entry,
            alt_start=request.alt_start,
            alt_end=request.alt_end,
            alt_strand=request.alt_strand,
            product_min=request.product_min,
            product_max=request.product_max,
            primer_num_return=request.primer_num_return,
            max_markers=request.max_markers,
            enzymes=request.enzymes,
            enzymes_per_primer=request.enzymes_per_primer,
            max_cuts_per_allele=request.max_cuts_per_allele,
            min_fragment_len=request.min_fragment_len,
            require_perfect_primers_in_alt=request.require_perfect_primers_in_alt,
            blast_check_dbs=request.blast_check_dbs,
            blast_num_threads=request.blast_num_threads,
            blast_max_target_seqs=request.blast_max_target_seqs,
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
    except (ValueError, blast_service.BlastExecutionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    markers = [CapsMarkerRow(**m) for m in (payload.get("markers") or [])]
    return CapsDesignResponse(
        ref_db=payload["ref_db"],
        ref_entry=payload["ref_entry"],
        ref_start=payload["ref_start"],
        ref_end=payload["ref_end"],
        ref_length=payload["ref_length"],
        alt_db=payload["alt_db"],
        alt_entry=payload["alt_entry"],
        alt_start=payload["alt_start"],
        alt_end=payload["alt_end"],
        alt_strand=payload["alt_strand"],
        alt_length=payload["alt_length"],
        mapped_by_blast=payload["mapped_by_blast"],
        primer_pairs_generated=payload["primer_pairs_generated"],
        markers=markers,
        warnings=payload.get("warnings") or [],
    )


@router.post("/design_job", response_model=JobCreateResponse)
async def design_caps_job(request: CapsDesignRequest) -> JobCreateResponse:
    """
    CAPS 生成をバックグラウンドジョブとして実行する。
    """

    def work(job: job_service.Job):
        return caps_service.design_caps_markers(
            ref_db=request.ref_db,
            ref_entry=request.ref_entry,
            ref_start=request.ref_start,
            ref_end=request.ref_end,
            alt_db=request.alt_db,
            map_alt_by_blast=request.map_alt_by_blast,
            alt_entry=request.alt_entry,
            alt_start=request.alt_start,
            alt_end=request.alt_end,
            alt_strand=request.alt_strand,
            product_min=request.product_min,
            product_max=request.product_max,
            primer_num_return=request.primer_num_return,
            max_markers=request.max_markers,
            enzymes=request.enzymes,
            enzymes_per_primer=request.enzymes_per_primer,
            max_cuts_per_allele=request.max_cuts_per_allele,
            min_fragment_len=request.min_fragment_len,
            require_perfect_primers_in_alt=request.require_perfect_primers_in_alt,
            blast_check_dbs=request.blast_check_dbs,
            blast_num_threads=request.blast_num_threads,
            blast_max_target_seqs=request.blast_max_target_seqs,
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
            progress_cb=lambda p, m=None: job.update(progress=p, message=m),
            cancel_cb=job.is_cancel_requested,
        )

    try:
        job = job_service.submit_job("caps_design", work)
    except job_service.JobQueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        # submit 自体は軽い想定だが、念のため
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JobCreateResponse(job_id=job.id)

