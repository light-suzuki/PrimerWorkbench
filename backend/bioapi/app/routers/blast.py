"""
BLAST ラッパ API ルーター。

MVP として、同期的に blastn を実行し結果を返すエンドポイントのみを実装する。
"""

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..core.paths import blast_databases_dir
from ..models.schemas import (
    BlastRequest,
    BlastMultiRequest,
    BlastResponse,
    BlastHitModel,
    BlastOrRequest,
    BlastOrResponse,
    BlastOrHitModel,
    BlastBatchLocalRequest,
    BlastBatchLocalResponse,
    BlastFetchSequenceRequest,
    BlastFetchSequenceResponse,
    BlastGeneLocationsRequest,
    BlastGeneLocationItem,
    BuildChromAliasesRequest,
    LocalBlastDbInfo,
    BlastDbChromosome,
    BlastDbEntrySearchHit,
    BlastRegionGeneModelResponse,
    JobCreateResponse,
)
from ..services import blast_service
from ..services import job_service


router = APIRouter(prefix="/blast", tags=["blast"])
logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        val = default
    else:
        try:
            val = int(raw)
        except ValueError:
            val = default
    return max(minimum, val)


def _env_limit(name: str, default: int = 0) -> int | None:
    """
    上限値（int）を環境変数から読む。

    - 0 以下: 上限なし（None）
    - 正数: その値を上限として返す
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        val = default
    else:
        try:
            val = int(raw)
        except ValueError:
            val = default
    if val <= 0:
        return None
    return val


def _normalize_seq(raw: str) -> str:
    return "".join((raw or "").split()).upper()


_BLAST_RUN_MAX_CONCURRENCY = _env_int("BLAST_RUN_MAX_CONCURRENCY", 2, minimum=1)
_BLAST_RUN_SEMAPHORE = asyncio.Semaphore(_BLAST_RUN_MAX_CONCURRENCY)


def _validate_batch_workload(
    seqs: list[str],
    dbs: list[str],
    *,
    allow_large_workload: bool = False,
) -> None:
    if allow_large_workload:
        return

    max_sequences = _env_limit("BLAST_BATCH_MAX_SEQUENCES", 0)
    max_total_bp = _env_limit("BLAST_BATCH_MAX_TOTAL_BP", 0)
    max_dbs = _env_limit("BLAST_BATCH_MAX_DBS", 0)
    max_work_units = _env_limit("BLAST_BATCH_MAX_WORK_UNITS", 0)

    if max_sequences is not None and len(seqs) > max_sequences:
        raise HTTPException(
            status_code=400,
            detail=f"sequences が多すぎます（{len(seqs)} > {max_sequences}）。分割して実行してください。",
        )

    total_bp = sum(len(s) for s in seqs)
    if max_total_bp is not None and total_bp > max_total_bp:
        raise HTTPException(
            status_code=400,
            detail=f"クエリ総塩基長が大きすぎます（{total_bp}bp > {max_total_bp}bp）。分割して実行してください。",
        )

    if max_dbs is not None and len(dbs) > max_dbs:
        raise HTTPException(
            status_code=400,
            detail=f"dbs が多すぎます（{len(dbs)} > {max_dbs}）。DB数を減らしてください。",
        )

    work_units = len(seqs) * len(dbs)
    if max_work_units is not None and work_units > max_work_units:
        raise HTTPException(
            status_code=400,
            detail=(
                f"処理量が上限を超えています（sequences*dbs={work_units} > {max_work_units}）。"
                "配列数またはDB数を減らしてください。"
            ),
        )


def _batch_result_hit_cap() -> int:
    return _env_int("BLAST_BATCH_MAX_RETURN_HITS_PER_QUERY", 20_000)


def _effective_batch_max_hsps(
    requested_max_hsps: int | None,
    seqs: list[str],
    task: str,
) -> int | None:
    if requested_max_hsps is not None and requested_max_hsps > 0:
        return requested_max_hsps

    task_norm = (task or "").strip().lower()
    if task_norm not in {"blastn-short", "blastn", "megablast", "dc-megablast"}:
        return requested_max_hsps

    raw_bp = (os.getenv("BLAST_BATCH_AUTO_MAX_HSPS_SHORT_QUERY_BP") or "").strip()
    try:
        short_query_bp = int(raw_bp) if raw_bp else 120
    except ValueError:
        short_query_bp = 120
    if short_query_bp <= 0:
        return requested_max_hsps

    longest = max((len(s) for s in seqs), default=0)
    if longest <= 0 or longest > short_query_bp:
        return requested_max_hsps

    raw_hsps = (os.getenv("BLAST_BATCH_AUTO_MAX_HSPS") or "").strip()
    try:
        auto_hsps = int(raw_hsps) if raw_hsps else 1
    except ValueError:
        auto_hsps = 1
    if auto_hsps <= 0:
        return requested_max_hsps
    return auto_hsps


def _validate_single_query_bp(seq_bp: int) -> None:
    max_single_bp = _env_limit("BLAST_SINGLE_MAX_BP", 0)
    if max_single_bp is not None and seq_bp > max_single_bp:
        raise HTTPException(
            status_code=400,
            detail=(
                f"クエリが大きすぎます（{seq_bp}bp > {max_single_bp}bp）。"
                " BLAST_SINGLE_MAX_BP を増やすか 0 で無制限にしてください。"
            ),
        )


def _meta_kwargs_from_result(raw_result: object, *, default_engine: str = "blast") -> dict[str, object]:
    meta = blast_service.extract_run_meta(raw_result, default_engine=default_engine)
    return blast_service.run_meta_to_dict(meta)


def _merge_equivalence_gates(gates: list[dict[str, object] | None]) -> dict[str, object] | None:
    valid = [g for g in gates if isinstance(g, dict)]
    if not valid:
        return None

    def avg_float(key: str) -> float | None:
        vals = [float(g[key]) for g in valid if g.get(key) is not None]
        if not vals:
            return None
        return sum(vals) / len(vals)

    compared_queries = sum(int(g.get("compared_queries") or 0) for g in valid)
    enabled_items = [g for g in valid if bool(g.get("enabled"))]
    enabled = bool(enabled_items)
    passed = all(bool(g.get("passed")) for g in enabled_items) if enabled_items else all(bool(g.get("passed", True)) for g in valid)

    notes = [str(g.get("note")) for g in valid if g.get("note")]
    threshold_top1 = next((g.get("threshold_top1_min") for g in valid if g.get("threshold_top1_min") is not None), None)
    threshold_top5 = next((g.get("threshold_top5_overlap_min") for g in valid if g.get("threshold_top5_overlap_min") is not None), None)
    threshold_bitscore = next(
        (g.get("threshold_bitscore_median_max_delta") for g in valid if g.get("threshold_bitscore_median_max_delta") is not None),
        None,
    )
    threshold_bitscore_ratio = next(
        (
            g.get("threshold_bitscore_median_max_delta_ratio")
            for g in valid
            if g.get("threshold_bitscore_median_max_delta_ratio") is not None
        ),
        None,
    )

    return {
        "enabled": enabled,
        "passed": passed,
        "top1_match_rate": avg_float("top1_match_rate"),
        "top5_overlap_rate": avg_float("top5_overlap_rate"),
        "bitscore_median_delta": avg_float("bitscore_median_delta"),
        "bitscore_median_delta_ratio": avg_float("bitscore_median_delta_ratio"),
        "compared_queries": compared_queries,
        "threshold_top1_min": threshold_top1,
        "threshold_top5_overlap_min": threshold_top5,
        "threshold_bitscore_median_max_delta": threshold_bitscore,
        "threshold_bitscore_median_max_delta_ratio": threshold_bitscore_ratio,
        "note": " / ".join(notes[:6]) if notes else None,
    }


def _merge_batch_meta(requested_engine: str, per_db_meta_rows: list[dict[str, object]]) -> dict[str, object]:
    requested = (requested_engine or "blast").strip().lower()
    if requested not in {"blast", "cuda"}:
        requested = "blast"

    executed = "cuda"
    fallback_used = False
    fallback_reasons: list[str] = []
    gate_rows: list[dict[str, object] | None] = []

    for row in per_db_meta_rows:
        eng = str(row.get("engine_executed") or "")
        if eng == "blast":
            executed = "blast"
        if bool(row.get("fallback_used")):
            fallback_used = True
            reason = str(row.get("fallback_reason") or "").strip()
            if reason:
                fallback_reasons.append(reason)
        gate_rows.append(row.get("equivalence_gate") if isinstance(row.get("equivalence_gate"), dict) else None)

    uniq_reasons: list[str] = []
    for r in fallback_reasons:
        if r not in uniq_reasons:
            uniq_reasons.append(r)

    return {
        "engine_requested": requested,
        "engine_executed": ("blast" if requested == "blast" else executed),
        "fallback_used": fallback_used,
        "fallback_reason": " / ".join(uniq_reasons[:4]) if uniq_reasons else None,
        "equivalence_gate": _merge_equivalence_gates(gate_rows),
    }


@router.get("/local_dbs", response_model=list[LocalBlastDbInfo])
async def list_local_dbs(db_type: str = "nucl") -> list[LocalBlastDbInfo]:
    """
    サーバー側で見つかったローカル BLAST DB の一覧を返す。

    - `~/sequence_workbench/blast_databases/*.nsq` を走査して DB プレフィックスを推定する。
    - UI 側の選択肢生成に利用する（ユーザーのパス手入力を減らす）。
    """
    base = blast_databases_dir()
    if not base.exists():
        return []

    kind = (db_type or "nucl").strip().lower()
    if kind not in {"nucl", "prot", "all"}:
        raise HTTPException(status_code=400, detail='db_type は "nucl" / "prot" / "all" のいずれかを指定してください。')

    prefixes: list[tuple[str, Path]] = []
    if kind in {"nucl", "all"}:
        for nsq in base.glob("*.nsq"):
            prefixes.append(("nucl", nsq.with_suffix("")))
    if kind in {"prot", "all"}:
        for psq in base.glob("*.psq"):
            prefixes.append(("prot", psq.with_suffix("")))

    # 重複除去（順序保持）
    uniq: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for dtype, p in sorted(prefixes, key=lambda x: (x[0], str(x[1]))):
        s = f"{dtype}|{p}"
        if s in seen:
            continue
        seen.add(s)
        uniq.append((dtype, p))

    out: list[LocalBlastDbInfo] = []
    for dtype, p in uniq:
        label = p.name
        fasta_candidates = [
            p.with_suffix(".fa"),
            p.with_suffix(".fasta"),
            p.with_suffix(".fna"),
            p.with_suffix(".faa"),
            p.with_suffix(".pep.fa"),
        ]
        has_fasta = any(x.exists() for x in fasta_candidates)

        # best-effort guess based on blast_service's known mapping
        has_annot = blast_service.guess_db_gff_path(str(p)) is not None

        out.append(
            LocalBlastDbInfo(
                label=label,
                path=str(p),
                has_fasta=has_fasta,
                has_annotation=has_annot,
                db_type=dtype,
            )
        )
    return out


@router.get("/db_chromosomes", response_model=list[BlastDbChromosome])
async def list_db_chromosomes(db: str) -> list[BlastDbChromosome]:
    """
    DB で利用できる染色体ラベル（chr1 など）→実体 entry の対応を返す。

    - DB により、実体 seqid が 1LG6 / NC_... / chr1 のように異なるため、
      UI のプルダウン用途に提供する。
    """
    if not db:
        raise HTTPException(status_code=400, detail="db が空です。")
    try:
        mapping = blast_service.get_db_chromosome_aliases(db)
    except blast_service.BlastExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [BlastDbChromosome(chrom=c, entry=e) for c, e in mapping]


@router.get("/db_search_entries", response_model=list[BlastDbEntrySearchHit])
async def search_db_entries(db: str, q: str, limit: int = 50) -> list[BlastDbEntrySearchHit]:
    """
    DB 内の entry を簡易検索する（seqid/title 部分一致）。
    """
    if not db:
        raise HTTPException(status_code=400, detail="db が空です。")
    q = (q or "").strip()
    if not q:
        return []
    limit = max(1, min(200, int(limit)))
    try:
        hits = blast_service.search_db_entries(db, q, limit=limit)
    except blast_service.BlastExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [
        BlastDbEntrySearchHit(
            entry=h.get("entry") or "",
            title=h.get("title"),
            chrom=h.get("chrom"),
            length=h.get("length"),
        )
        for h in hits
        if h.get("entry")
    ]


@router.get("/region_gene_model", response_model=BlastRegionGeneModelResponse)
async def get_region_gene_model(
    db: str,
    entry: str,
    start: int,
    end: int,
    gene_hint: str | None = None,
    max_genes: int = 3,
) -> BlastRegionGeneModelResponse:
    """
    ローカル GFF3 を使って、指定領域に重なる gene の exon/CDS を返す（個人利用向け・best effort）。

    - start/end は genome 座標（1-based, inclusive）
    - gene_hint が与えられた場合は候補 gene の絞り込みに利用する
    """
    if not db:
        raise HTTPException(status_code=400, detail="db が空です。")
    if not entry:
        raise HTTPException(status_code=400, detail="entry が空です。")
    if start < 1 or end < 1:
        raise HTTPException(status_code=400, detail="start/end は 1 以上を指定してください。")
    max_genes = max(1, min(10, int(max_genes)))

    try:
        model = blast_service.get_region_gene_model(
            db=db,
            entry=entry,
            start=start,
            end=end,
            gene_hint=gene_hint,
            max_genes=max_genes,
        )
    except blast_service.BlastExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return BlastRegionGeneModelResponse(**model)


@router.post("/gene_locations", response_model=list[BlastGeneLocationItem])
async def gene_locations(request: BlastGeneLocationsRequest) -> list[BlastGeneLocationItem]:
    """
    GFF3 から gene の染色体/座標を返す（個人利用向け・best effort）。

    - ids は gene_id / gene_name のどちらでも良い（正規化して照合）
    - DBごとにまとめて照会できる（queries）
    """
    queries = request.queries or []
    if not queries:
        raise HTTPException(status_code=400, detail="queries が空です。")

    out: list[BlastGeneLocationItem] = []
    for q in queries:
        db = (q.db or "").strip()
        ids = q.ids or []
        if not db:
            continue
        if not ids:
            continue
        max_ids = _env_limit("BLAST_GENE_LOCATIONS_MAX_IDS_PER_QUERY", 0)
        if max_ids is not None and len(ids) > max_ids:
            raise HTTPException(status_code=400, detail=f"ids が多すぎます（{len(ids)} > {max_ids} / query）。")
        items = blast_service.get_gene_locations(db, ids)
        for it in items:
            out.append(BlastGeneLocationItem(**it))
    return out


@router.post("/run", response_model=BlastResponse)
async def run_blast(request: BlastRequest) -> BlastResponse:
    """
    BLAST を 1 つのバックエンドで実行するエンドポイント。

    - backend=local の場合: ローカルの BLAST+ を利用
    - backend=ncbi の場合: NCBI の BLAST URL API を利用（nt データベース）
    """
    backend = (request.backend or "local").lower()
    seq = (request.sequence or "").strip()
    if not seq:
        raise HTTPException(status_code=400, detail="クエリ配列が空です。")
    seq_bp = len(_normalize_seq(seq))
    _validate_single_query_bp(seq_bp)

    response_meta: dict[str, object] = {}
    try:
        async with _BLAST_RUN_SEMAPHORE:
            if backend == "ncbi":
                if request.ncbi_targets:
                    hits = []
                    for tgt in request.ncbi_targets:
                        label = tgt.get("label") or "ncbi"
                        db = tgt.get("database") or request.ncbi_database
                        q = tgt.get("entrez_query") or request.ncbi_entrez_query
                        part_hits = await asyncio.to_thread(
                            blast_service.run_blast_ncbi,
                            seq,
                            request.task,
                            db,
                            request.evalue,
                            request.max_target_seqs,
                            q,
                        )
                        for h in part_hits:
                            h.source = f"ncbi:{label}"
                        hits.extend(part_hits)
                else:
                    hits = await asyncio.to_thread(
                        blast_service.run_blast_ncbi,
                        seq,
                        request.task,
                        request.ncbi_database,
                        request.evalue,
                        request.max_target_seqs,
                        request.ncbi_entrez_query,
                    )
            elif backend == "ensembl":
                hits = await asyncio.to_thread(
                    blast_service.run_blast_ensembl,
                    seq,
                    request.ensembl_species or "homo_sapiens",
                    "core",
                    request.evalue,
                    request.max_target_seqs,
                )
            elif backend == "local":
                raw_hits = await asyncio.to_thread(
                    blast_service.run_blastn_sync,
                    seq,
                    request.db,
                    request.task,
                    request.evalue,
                    request.max_target_seqs,
                    request.num_threads,
                    request.max_hsps,
                    request.local_mode,
                    request.engine,
                )
                hits = list(raw_hits)
                response_meta = _meta_kwargs_from_result(raw_hits, default_engine=request.engine)
            else:
                raise HTTPException(
                    status_code=400,
                    detail='backend は "local" / "ncbi" / "ensembl" のいずれかを指定してください。',
                )
    except blast_service.BlastExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    hit_models = [
        BlastHitModel(
            qseqid=h.qseqid,
            sseqid=h.sseqid,
            pident=h.pident,
            length=h.length,
            mismatch=h.mismatch,
            gapopen=h.gapopen,
            qstart=h.qstart,
            qend=h.qend,
            sstart=h.sstart,
            send=h.send,
            evalue=h.evalue,
            bitscore=h.bitscore,
            source=getattr(h, "source", None) or backend,
            subject_title=h.subject_title,
            subject_chrom=h.subject_chrom,
            subject_length=h.subject_length,
            gene_ids=h.gene_ids,
            gene_names=h.gene_names,
        )
        for h in hits
    ]

    return BlastResponse(
        num_hits=len(hit_models),
        hits=hit_models,
        **response_meta,
    )


@router.post("/run_or", response_model=BlastOrResponse)
async def run_blast_or(request: BlastOrRequest) -> BlastOrResponse:
    """
    BLAST-OR（アラインメント表示用）を実行する。

    - local BLAST+ のみ対応（1 クエリ × 1 DB）
    - outfmt=6 + qseq/sseq を返す
    """
    seq = (request.sequence or "").strip()
    if not seq:
        raise HTTPException(status_code=400, detail="クエリ配列が空です。")
    seq_bp = len(_normalize_seq(seq))
    _validate_single_query_bp(seq_bp)
    if not (request.db or "").strip():
        raise HTTPException(status_code=400, detail="db が空です。")

    try:
        async with _BLAST_RUN_SEMAPHORE:
            hits = await asyncio.to_thread(
                blast_service.run_blast_with_alignment_sync,
                request.program,
                seq,
                request.db,
                request.task,
                request.evalue,
                request.max_target_seqs,
                request.num_threads,
                request.max_hsps,
                request.local_mode,
                "blast",
            )
    except blast_service.BlastExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    hit_models = [
        BlastOrHitModel(
            qseqid=h.qseqid,
            sseqid=h.sseqid,
            pident=h.pident,
            length=h.length,
            mismatch=h.mismatch,
            gapopen=h.gapopen,
            qstart=h.qstart,
            qend=h.qend,
            sstart=h.sstart,
            send=h.send,
            evalue=h.evalue,
            bitscore=h.bitscore,
            source="local",
            subject_title=h.subject_title,
            subject_chrom=h.subject_chrom,
            subject_length=h.subject_length,
            gene_ids=h.gene_ids,
            gene_names=h.gene_names,
            qseq=h.qseq or "",
            sseq=h.sseq or "",
        )
        for h in hits
    ]

    return BlastOrResponse(num_hits=len(hit_models), hits=hit_models)


@router.post("/run_or_job", response_model=JobCreateResponse)
async def run_blast_or_job(request: BlastOrRequest) -> JobCreateResponse:
    """
    BLAST-OR（アラインメント表示用）をバックグラウンドジョブとして実行する。

    - local BLAST+ のみ対応（1 クエリ × 1 DB）
    - 進捗（%）はステージ単位の概算（BLAST 実行中は増えにくい）
    """
    seq = (request.sequence or "").strip()
    if not seq:
        raise HTTPException(status_code=400, detail="クエリ配列が空です。")
    if not (request.db or "").strip():
        raise HTTPException(status_code=400, detail="db が空です。")

    def work(job: job_service.Job):
        job.update(progress=0.02, message="starting")
        job.raise_if_cancel_requested()

        job.update(progress=0.1, message="running blast")
        hits = blast_service.run_blast_with_alignment_sync(
            program=request.program,
            sequence=seq,
            db=request.db,
            task=request.task,
            evalue=request.evalue,
            max_target_seqs=request.max_target_seqs,
            num_threads=request.num_threads,
            max_hsps=request.max_hsps,
            local_mode=request.local_mode,
            engine="blast",
        )
        job.raise_if_cancel_requested()

        job.update(progress=0.9, message="formatting")
        hit_rows = [
            {
                "qseqid": h.qseqid,
                "sseqid": h.sseqid,
                "pident": h.pident,
                "length": h.length,
                "mismatch": h.mismatch,
                "gapopen": h.gapopen,
                "qstart": h.qstart,
                "qend": h.qend,
                "sstart": h.sstart,
                "send": h.send,
                "evalue": h.evalue,
                "bitscore": h.bitscore,
                "source": "local",
                "subject_title": h.subject_title,
                "subject_chrom": h.subject_chrom,
                "subject_length": h.subject_length,
                "gene_ids": h.gene_ids,
                "gene_names": h.gene_names,
                "qseq": h.qseq or "",
                "sseq": h.sseq or "",
            }
            for h in hits
        ]
        return {"num_hits": len(hit_rows), "hits": hit_rows}

    try:
        job = job_service.submit_job("blast_or", work)
    except job_service.JobQueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JobCreateResponse(job_id=job.id)


@router.post("/run_multi", response_model=BlastResponse)
async def run_blast_multi(request: BlastMultiRequest) -> BlastResponse:
    """
    複数バックエンドで BLAST を並列実行し、結果をまとめて返すエンドポイント。

    - backends に "local" / "ncbi" を指定可能（将来他のエンジンも追加予定）。
    """

    backends = [b.lower() for b in request.backends if b]
    if not backends:
        raise HTTPException(
            status_code=400,
            detail="少なくとも 1 つは BLAST の実行先を選択してください。",
        )

    seq = (request.sequence or "").strip()
    if not seq:
        raise HTTPException(status_code=400, detail="クエリ配列が空です。")
    seq_bp = len(_normalize_seq(seq))
    _validate_single_query_bp(seq_bp)

    hit_models: list[BlastHitModel] = []
    local_meta: dict[str, object] | None = None

    # local は先に実行してすぐ返せるように順番を固定（asyncio.to_thread で順次）
    for name in backends:
        if name == "local":
            try:
                async with _BLAST_RUN_SEMAPHORE:
                    raw_hits = await asyncio.to_thread(
                        blast_service.run_blastn_sync,
                        seq,
                        request.db,
                        request.task,
                        request.evalue,
                        request.max_target_seqs,
                        request.num_threads,
                        request.max_hsps,
                        request.local_mode,
                        request.engine,
                    )
                    hits = list(raw_hits)
                    local_meta = _meta_kwargs_from_result(raw_hits, default_engine=request.engine)
            except blast_service.BlastExecutionError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            for h in hits:
                hit_models.append(
                    BlastHitModel(
                        qseqid=h.qseqid,
                        sseqid=h.sseqid,
                        pident=h.pident,
                        length=h.length,
                        mismatch=h.mismatch,
                        gapopen=h.gapopen,
                        qstart=h.qstart,
                        qend=h.qend,
                        sstart=h.sstart,
                        send=h.send,
                        evalue=h.evalue,
                        bitscore=h.bitscore,
                        source="local",
                        subject_title=h.subject_title,
                        subject_chrom=h.subject_chrom,
                        subject_length=h.subject_length,
                        gene_ids=h.gene_ids,
                        gene_names=h.gene_names,
                    )
                )

    # NCBI / Ensembl は並列でまとめて取得
    async def _run_nonlocal(name: str):
        try:
            if name == "ncbi":
                if request.ncbi_targets:
                    collected = []
                    for tgt in request.ncbi_targets:
                        label = tgt.get("label") or "ncbi"
                        db = tgt.get("database") or request.ncbi_database
                        q = tgt.get("entrez_query") or request.ncbi_entrez_query
                        part = await asyncio.to_thread(
                            blast_service.run_blast_ncbi,
                            seq,
                            request.task,
                            db,
                            request.evalue,
                            request.max_target_seqs,
                            q,
                        )
                        for h in part:
                            h.source = f"ncbi:{label}"
                        collected.extend(part)
                    return collected
                return await asyncio.to_thread(
                    blast_service.run_blast_ncbi,
                    seq,
                    request.task,
                    request.ncbi_database,
                    request.evalue,
                    request.max_target_seqs,
                    request.ncbi_entrez_query,
                )
            if name == "ensembl":
                return await asyncio.to_thread(
                    blast_service.run_blast_ensembl,
                    seq,
                    request.ensembl_species or "homo_sapiens",
                    "core",
                    request.evalue,
                    request.max_target_seqs,
                )
            return []
        except blast_service.BlastExecutionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    nonlocal_backends = [b for b in backends if b != "local"]
    results = await asyncio.gather(*[_run_nonlocal(b) for b in nonlocal_backends])

    for backend_name, hits in zip(nonlocal_backends, results, strict=False):
        for h in hits:
            hit_models.append(
                BlastHitModel(
                    qseqid=h.qseqid,
                    sseqid=h.sseqid,
                    pident=h.pident,
                    length=h.length,
                    mismatch=h.mismatch,
                    gapopen=h.gapopen,
                    qstart=h.qstart,
                    qend=h.qend,
                    sstart=h.sstart,
                    send=h.send,
                    evalue=h.evalue,
                    bitscore=h.bitscore,
                    source=getattr(h, "source", None) or backend_name,
                    subject_title=h.subject_title,
                    subject_chrom=h.subject_chrom,
                    subject_length=h.subject_length,
                    gene_ids=h.gene_ids,
                    gene_names=h.gene_names,
                )
            )

    hit_models.sort(key=lambda h: (-h.bitscore, -h.pident))

    if local_meta is not None and len(nonlocal_backends) == 0:
        return BlastResponse(num_hits=len(hit_models), hits=hit_models, **local_meta)
    return BlastResponse(num_hits=len(hit_models), hits=hit_models)


@router.post("/run_batch_local", response_model=BlastBatchLocalResponse)
async def run_blast_batch_local(request: BlastBatchLocalRequest) -> BlastBatchLocalResponse:
    """
    ローカル BLAST+ のみを対象に、複数クエリ配列をまとめて実行するエンドポイント。

    - sequences の各要素について、指定されたすべての dbs に対するヒットを集約する。
    - 既存の /blast/run_multi の local 部分と同等の結果を、クエリごとに返す。
    """
    if not request.sequences:
        raise HTTPException(status_code=400, detail="sequences が空です。")
    if not request.dbs:
        raise HTTPException(status_code=400, detail="dbs が空です。ローカル DB を 1 つ以上指定してください。")

    seqs = [_normalize_seq(s) for s in request.sequences]
    if any(not s for s in seqs):
        raise HTTPException(status_code=400, detail="空のクエリ配列が含まれています。")
    _validate_batch_workload(
        seqs,
        request.dbs,
        allow_large_workload=request.allow_large_workload,
    )

    # per-db, per-seq の BlastHit を取得してから、seq ごとにマージする
    # 複数 DB を使う場合は並列に投げて待ち合わせる。
    max_parallel = request.max_parallel_dbs or int(os.getenv("BLAST_MAX_PARALLEL_DBS", "3"))
    max_parallel_cap = _env_int("BLAST_MAX_PARALLEL_DBS_CAP", 6)
    max_parallel = max(1, min(int(max_parallel), max_parallel_cap))
    parallel_jobs = min(len(request.dbs), max_parallel)
    threads = blast_service.clamp_blast_threads(request.num_threads, parallel_jobs=parallel_jobs)
    max_hsps = _effective_batch_max_hsps(request.max_hsps, seqs, request.task)
    db_plans = [blast_service.resolve_local_db_plan(db, request.local_mode) for db in request.dbs]

    # limit concurrency to avoid spawning too many heavy processes at once
    sem = asyncio.Semaphore(parallel_jobs)

    async def run_one(db_path: str, mode: str):
        async with sem:
            return await asyncio.to_thread(
                blast_service.run_blastn_batch_local,
                seqs,
                db_path,
                request.task,
                request.evalue,
                request.max_target_seqs,
                threads,
                max_hsps,
                mode,
                engine=getattr(request, "engine", "blast"),
            )

    tasks = [run_one(db_path, mode) for db_path, _label, mode in db_plans]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    per_db_results: list[list[list[blast_service.BlastHit]]] = []
    per_db_meta_rows: list[dict[str, object]] = []
    failed_dbs: list[str] = []
    for (_db_path, db_label, _mode), res in zip(db_plans, gathered, strict=False):
        if isinstance(res, Exception):
            failed_dbs.append(db_label)
            logger.warning("batch local blast failed on db=%s: %s", db_label, res)
            per_db_results.append([[] for _ in range(len(seqs))])
            per_db_meta_rows.append(
                {
                    "engine_requested": (request.engine or "blast"),
                    "engine_executed": "blast",
                    "fallback_used": (request.engine or "blast") == "cuda",
                    "fallback_reason": str(res),
                    "equivalence_gate": None,
                }
            )
        else:
            per_db_results.append(list(res))
            per_db_meta_rows.append(_meta_kwargs_from_result(res, default_engine=request.engine))

    if failed_dbs and len(failed_dbs) == len(db_plans):
        msg = " / ".join(failed_dbs[:6])
        if len(failed_dbs) > 6:
            msg += f" ... ({len(failed_dbs)} DBs)"
        raise HTTPException(
            status_code=500,
            detail=f"すべての DB で batch BLAST に失敗しました: {msg}",
        )

    merged_meta = _merge_batch_meta(request.engine, per_db_meta_rows)
    result_hit_cap = _batch_result_hit_cap()
    results: list[BlastResponse] = []
    for idx in range(len(seqs)):
        merged_hits: list[BlastHitModel] = []
        for (_db_path, db_label, _mode), hits_per_seq in zip(db_plans, per_db_results, strict=False):
            db_hits = hits_per_seq[idx]
            for h in db_hits:
                merged_hits.append(
                    BlastHitModel(
                        qseqid=h.qseqid,
                        sseqid=h.sseqid,
                        pident=h.pident,
                        length=h.length,
                        mismatch=h.mismatch,
                        gapopen=h.gapopen,
                        qstart=h.qstart,
                        qend=h.qend,
                        sstart=h.sstart,
                        send=h.send,
                        evalue=h.evalue,
                        bitscore=h.bitscore,
                        source=f"local:{db_label}",
                        subject_title=h.subject_title,
                        subject_chrom=h.subject_chrom,
                        subject_length=h.subject_length,
                        gene_ids=h.gene_ids,
                        gene_names=h.gene_names,
                    )
                )
        merged_hits.sort(key=lambda h: (-h.bitscore, -h.pident))
        if len(merged_hits) > result_hit_cap:
            merged_hits = merged_hits[:result_hit_cap]
        results.append(BlastResponse(num_hits=len(merged_hits), hits=merged_hits, **merged_meta))

    return BlastBatchLocalResponse(
        results=results,
        batch_meta=BlastResponse(num_hits=0, hits=[], **merged_meta),
    )


@router.post("/run_batch_local_job", response_model=JobCreateResponse)
async def run_blast_batch_local_job(request: BlastBatchLocalRequest) -> JobCreateResponse:
    """
    /blast/run_batch_local をバックグラウンドジョブとして実行する。
    """
    if not request.sequences:
        raise HTTPException(status_code=400, detail="sequences が空です。")
    if not request.dbs:
        raise HTTPException(status_code=400, detail="dbs が空です。ローカル DB を 1 つ以上指定してください。")

    seqs = [_normalize_seq(s) for s in request.sequences]
    if any(not s for s in seqs):
        raise HTTPException(status_code=400, detail="空のクエリ配列が含まれています。")
    _validate_batch_workload(
        seqs,
        request.dbs,
        allow_large_workload=request.allow_large_workload,
    )

    def work(job: job_service.Job):
        job.update(progress=0.02, message="batch blast: starting")
        job.raise_if_cancel_requested()

        # per-db, per-seq の BlastHit を取得してから、seq ごとにマージする
        per_db_results: list[list[list[blast_service.BlastHit]] | None] = [None] * len(request.dbs)
        per_db_meta_rows: list[dict[str, object] | None] = [None] * len(request.dbs)
        failed_dbs: list[str] = []

        env_max = int(os.getenv("BLAST_MAX_PARALLEL_DBS", "3"))
        max_workers = request.max_parallel_dbs or env_max
        max_parallel_cap = _env_int("BLAST_MAX_PARALLEL_DBS_CAP", 6)
        max_workers = max(1, min(int(max_workers), max_parallel_cap, len(request.dbs)))
        threads = blast_service.clamp_blast_threads(request.num_threads, parallel_jobs=max_workers)
        max_hsps = _effective_batch_max_hsps(request.max_hsps, seqs, request.task)
        from concurrent.futures import ThreadPoolExecutor, as_completed

        db_plans = [blast_service.resolve_local_db_plan(db, request.local_mode) for db in request.dbs]
        job.update(
            progress=0.03,
            message=f"batch blast: {len(request.dbs)} dbs / parallel={max_workers} / threads={threads}",
        )
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(
                    blast_service.run_blastn_batch_local,
                    seqs,
                    db_path,
                    request.task,
                    request.evalue,
                    request.max_target_seqs,
                    threads,
                    max_hsps,
                    mode,
                    engine=getattr(request, "engine", "blast"),
                ): (i, db_label)
                for i, (db_path, db_label, mode) in enumerate(db_plans)
            }
            done = 0
            for fut in as_completed(futures):
                job.raise_if_cancel_requested()
                i, db_label = futures[fut]
                try:
                    hits_per_seq = fut.result()
                    per_db_meta_rows[i] = _meta_kwargs_from_result(hits_per_seq, default_engine=request.engine)
                except Exception as exc:
                    failed_dbs.append(db_label)
                    logger.warning("batch local blast job failed on db=%s: %s", db_label, exc)
                    hits_per_seq = [[] for _ in range(len(seqs))]
                    per_db_meta_rows[i] = {
                        "engine_requested": (request.engine or "blast"),
                        "engine_executed": "blast",
                        "fallback_used": (request.engine or "blast") == "cuda",
                        "fallback_reason": str(exc),
                        "equivalence_gate": None,
                    }
                per_db_results[i] = hits_per_seq
                done += 1
                job.update(
                    progress=0.02 + 0.7 * (done / max(1, len(request.dbs))),
                    message=f"batch blast: {done}/{len(request.dbs)} dbs (parallel={max_workers}, threads={threads})",
                )

        if failed_dbs and len(failed_dbs) == len(request.dbs):
            msg = " / ".join(failed_dbs[:6])
            if len(failed_dbs) > 6:
                msg += f" ... ({len(failed_dbs)} DBs)"
            raise blast_service.BlastExecutionError(
                f"すべての DB で batch BLAST に失敗しました: {msg}"
            )

        # mypy 安全化（上のループで必ず埋まる想定）
        safe_per_db_results: list[list[list[blast_service.BlastHit]]] = [
            (hits_per_seq if hits_per_seq is not None else [[] for _ in range(len(seqs))])
            for hits_per_seq in per_db_results
        ]
        safe_meta_rows: list[dict[str, object]] = [
            (m if isinstance(m, dict) else {
                "engine_requested": (request.engine or "blast"),
                "engine_executed": "blast",
                "fallback_used": False,
                "fallback_reason": None,
                "equivalence_gate": None,
            })
            for m in per_db_meta_rows
        ]
        merged_meta = _merge_batch_meta(request.engine, safe_meta_rows)

        result_hit_cap = _batch_result_hit_cap()
        results: list[dict] = []
        for idx in range(len(seqs)):
            merged_hits: list[dict] = []
            for (_db_path, db_label, _mode), hits_per_seq in zip(db_plans, safe_per_db_results, strict=False):
                db_hits = hits_per_seq[idx]
                for h in db_hits:
                    merged_hits.append(
                        {
                            "qseqid": h.qseqid,
                            "sseqid": h.sseqid,
                            "pident": h.pident,
                            "length": h.length,
                            "mismatch": h.mismatch,
                            "gapopen": h.gapopen,
                            "qstart": h.qstart,
                            "qend": h.qend,
                            "sstart": h.sstart,
                            "send": h.send,
                            "evalue": h.evalue,
                            "bitscore": h.bitscore,
                            "source": f"local:{db_label}",
                            "subject_title": h.subject_title,
                            "subject_chrom": h.subject_chrom,
                            "subject_length": h.subject_length,
                            "gene_ids": h.gene_ids,
                            "gene_names": h.gene_names,
                        }
                    )
            merged_hits.sort(key=lambda h: (-(h.get("bitscore") or 0), -(h.get("pident") or 0)))
            if len(merged_hits) > result_hit_cap:
                merged_hits = merged_hits[:result_hit_cap]
            results.append({
                "num_hits": len(merged_hits),
                "hits": merged_hits,
                **merged_meta,
            })

            if idx % 20 == 0:
                job.update(progress=0.75 + 0.2 * (idx / max(1, len(seqs))), message="merging results")
                job.raise_if_cancel_requested()

        done_msg = "done"
        if failed_dbs:
            done_msg = f"done (failed_dbs={len(failed_dbs)})"
        job.update(progress=1.0, message=done_msg)
        return {
            "results": results,
            "batch_meta": {
                "num_hits": 0,
                "hits": [],
                **merged_meta,
            },
        }

    try:
        job = job_service.submit_job("blast_batch_local", work)
    except job_service.JobQueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JobCreateResponse(job_id=job.id)


@router.post("/fetch_sequence", response_model=BlastFetchSequenceResponse)
async def fetch_sequence_from_local_db(
    request: BlastFetchSequenceRequest,
) -> BlastFetchSequenceResponse:
    """
    ローカル BLAST DB (makeblastdb) から配列（または部分配列）を取り出す。

    主に、プライマーBLASTで得た subject と座標から参照配列（amplicon）を構築する用途を想定。
    """
    try:
        seq = await asyncio.to_thread(
            blast_service.fetch_sequence_local_db,
            request.db,
            request.entry,
            request.start,
            request.end,
            request.strand,
        )
    except blast_service.BlastInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except blast_service.BlastNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except blast_service.BlastExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return BlastFetchSequenceResponse(
        db=request.db,
        entry=request.entry,
        start=request.start,
        end=request.end,
        strand=request.strand,
        length=len(seq),
        sequence=seq,
    )


@router.post("/build_chrom_aliases_job", response_model=JobCreateResponse)
async def build_chrom_aliases_job(request: BuildChromAliasesRequest) -> JobCreateResponse:
    """
    染色体情報（chrN）を持たないDBに対して、参照DB（例: reference_v1）を使って
    entry→chr の対応を best-effort で推定し、キャッシュする。

    - 推定結果はバックエンドの tmp に保存され、以降 /blast/db_chromosomes や entry 解決で利用される。
    """
    db = (request.db or "").strip()
    ref_db = (request.ref_db or "").strip() or "reference_v1"
    if not db:
        raise HTTPException(status_code=400, detail="db が空です。")

    def work(job: job_service.Job):
        job.update(progress=0.02, message="preparing")
        job.raise_if_cancel_requested()
        res = blast_service.build_chrom_alias_overrides(
            db=db,
            ref_db=ref_db,
            max_entries=request.max_entries,
            sample_bp=request.sample_bp,
            samples_per_entry=request.samples_per_entry,
            job=job,
        )
        return res

    try:
        job = job_service.submit_job("build_chrom_aliases", work)
    except job_service.JobQueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JobCreateResponse(job_id=job.id)


