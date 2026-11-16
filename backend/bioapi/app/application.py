"""Transport-independent experiment operations shared by CLI and HTTP adapters."""
from pathlib import Path
import os
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict

from .models.schemas import (SequenceBasicAnalysisRequest, SequenceBasicAnalysisResponse,
    OrfAnalysisRequest, OrfAnalysisResponse, RestrictionAnalysisRequest, RestrictionAnalysisResponse,
    PrimerDesignRequest, PrimerDesignResponse, CapsDesignRequest, CapsDesignResponse,
    BlastFetchSequenceRequest, BlastFetchSequenceResponse, BlastRequest, BlastResponse, BlastHitModel)
from .services import sequence_service, primer_service, caps_service, blast_service
from .services.enzyme_catalog import catalog
from .services.restriction_detail import enzyme_detail
from .services.gene_service import local_structure_from_gff
from .services.primer_engine import PrimerEngineRequest, design as primerblast_design


class LocalGeneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gene_id: str
    gff_path: str
    fasta_path: str
    species: str | None = None


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalBlastRequest(BlastRequest):
    backend: Literal["local"] = "local"


def sequence_basic(request):
    return SequenceBasicAnalysisResponse(**sequence_service.analyze_basic(**request.model_dump()))


def sequence_orfs(request):
    return OrfAnalysisResponse(orfs=sequence_service.find_orfs(**request.model_dump()))


def sequence_restriction(request):
    if not request.enzymes:
        raise ValueError("At least one enzyme is required.")
    sites = sequence_service.analyze_restriction_sites(request.sequence, request.enzymes)
    normalized = sequence_service._normalize_sequence(request.sequence)
    return RestrictionAnalysisResponse(sequence_length=len("".join(request.sequence.split())),
        results=[{"enzyme": name, "cut_positions": positions,
                  "enzyme_detail": enzyme_detail(name, normalized)} for name, positions in sites.items()])


def primers_design(request):
    params = request.model_dump()
    params["target_start_1based"] = params.pop("target_start")
    candidates = primer_service.design_primers(**params)
    return PrimerDesignResponse(sequence_length=len("".join(request.sequence.split())),
        num_candidates=len(candidates), product_size_range=request.product_size_range, candidates=candidates)


def caps_payload(request, *, progress_cb=None, cancel_cb=None):
    return caps_service.design_caps_markers(**request.model_dump(), progress_cb=progress_cb, cancel_cb=cancel_cb)


def caps_design(request):
    return CapsDesignResponse(**caps_payload(request))


def sequence_fetch(request):
    params = request.model_dump()
    sequence = blast_service.fetch_sequence_local_db(**params)
    return BlastFetchSequenceResponse(**params, sequence=sequence, length=len(sequence))


def gene_structure(request):
    return local_structure_from_gff(request.gene_id, request.species, Path(request.gff_path), Path(request.fasta_path))


def blast_local(request):
    if (request.backend or "local").lower() != "local":
        raise ValueError("blast.local accepts only a local database")
    sequence = request.sequence.strip()
    if not sequence:
        raise ValueError("Query sequence is empty")
    try:
        limit = int(os.getenv("BLAST_SINGLE_MAX_BP") or "0")
    except ValueError:
        limit = 0
    if limit > 0 and len("".join(sequence.split())) > limit:
        raise ValueError("Query exceeds BLAST_SINGLE_MAX_BP")
    raw = blast_service.run_blastn_sync(sequence, request.db, request.task, request.evalue,
        request.max_target_seqs, request.num_threads, request.max_hsps, request.local_mode, request.engine)
    hits = [BlastHitModel(**{**vars(hit), "source": getattr(hit, "source", None) or "local"}) for hit in raw]
    meta = blast_service.extract_run_meta(raw, default_engine=request.engine)
    return BlastResponse(num_hits=len(hits), hits=hits, **blast_service.run_meta_to_dict(meta))


OPERATIONS: dict[str, tuple[type[BaseModel], Callable]] = {
    "primerblast.design": (PrimerEngineRequest, primerblast_design),
    "sequence.basic": (SequenceBasicAnalysisRequest, sequence_basic),
    "sequence.orfs": (OrfAnalysisRequest, sequence_orfs),
    "sequence.restriction": (RestrictionAnalysisRequest, sequence_restriction),
    "primers.design": (PrimerDesignRequest, primers_design),
    "caps.design": (CapsDesignRequest, caps_design),
    "sequence.fetch": (BlastFetchSequenceRequest, sequence_fetch),
    "gene.structure": (LocalGeneRequest, gene_structure),
    "blast.local": (LocalBlastRequest, blast_local),
    "enzymes.catalog": (EmptyRequest, lambda _: catalog()),
}


def capabilities():
    return {"schema_version": "gene-research-agent/1", "execution": "local",
            "starts_gui": False, "uploads_data": False,
            "operations": [{"name": name, "input_schema": {**model.model_json_schema(), "additionalProperties": False},
                            "writes_database": False} for name, (model, _) in OPERATIONS.items()]}


def execute(operation, params):
    if operation not in OPERATIONS:
        raise ValueError("Unknown operation: " + str(operation))
    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    model, handler = OPERATIONS[operation]
    unknown = set(params) - set(model.model_fields)
    if unknown:
        raise ValueError("Unknown input fields: " + ", ".join(sorted(unknown)))
    result = handler(model.model_validate(params))
    return result.model_dump(mode="json") if isinstance(result, BaseModel) else result
