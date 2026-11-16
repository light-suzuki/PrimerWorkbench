"""
Ensembl REST を利用して Gene の構造（エキソン/CDS）とゲノム配列を取得するルーター。
"""

from __future__ import annotations

import gzip
import os
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from ..core.config import get_settings


router = APIRouter(prefix="/ensembl", tags=["ensembl"])


def _open_text_maybe_gzip(path: Path):
    return gzip.open(path, "rt") if path.suffix.endswith("gz") else path.open("r", encoding="utf-8")


def choose_transcript(transcripts: list[dict[str, Any]]) -> dict[str, Any]:
    """
    代表となる transcript を選ぶ。canonical があれば優先、なければ最長。
    """
    if not transcripts:
        return {}
    canonical = next((t for t in transcripts if t.get("is_canonical")), None)
    if canonical:
        return canonical
    return max(transcripts, key=lambda t: t.get("length") or 0)


def to_gene_structure(
    gene_json: dict[str, Any],
) -> tuple[str, int, int, int, list[dict[str, int]], list[dict[str, int]]]:
    """
    gene lookup 応答から start/end/strand と exon/CDS 配列を抽出し、gene 座標基準の 1-based で返す。
    """
    start = gene_json.get("start")
    end = gene_json.get("end")
    strand = gene_json.get("strand")
    if not all(isinstance(x, int) for x in (start, end, strand)):
        raise ValueError("gene 座標を解釈できませんでした。")

    transcripts = gene_json.get("Transcript") or []
    tx = choose_transcript(transcripts)
    exons: list[dict[str, int]] = []
    for ex in tx.get("Exon") or []:
        es, ee = ex.get("start"), ex.get("end")
        if isinstance(es, int) and isinstance(ee, int):
            exons.append({"start": es, "end": ee})

    cds_ranges: list[dict[str, int]] = []
    translation = tx.get("Translation") or {}
    ts, te = translation.get("start"), translation.get("end")
    if isinstance(ts, int) and isinstance(te, int):
        cds_ranges.append({"start": ts, "end": te})

    return gene_json.get("seq_region_name") or "", start, end, strand, exons, cds_ranges


from ..services.gene_service import ReferenceError, load_local_sequence
from ..services.gene_service import local_structure_from_gff as _local_structure


def local_structure_from_gff(gene_id, species, gff_path, fasta_path):
    try:
        return _local_structure(gene_id, species, gff_path, fasta_path)
    except ReferenceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/gene_structure/{gene_id}")
async def get_gene_structure(
    gene_id: str,
    species: str | None = Query(None, description="species 名（例: arabidopsis_thaliana）"),
) -> dict[str, Any]:
    """
    Gene ID を指定して、エキソン/CDS 範囲とゲノム配列を取得する。
    """
    settings = get_settings()
    base = settings.ensembl_rest_base_url.rstrip("/")

    async def _lookup(target_id: str, base_url: str) -> dict[str, Any]:
        lookup_params = {"expand": "1"}
        if species:
            lookup_params["species"] = species
        lookup_url = f"{base_url}/lookup/id/{target_id}"
        verify = False if "ensemblgenomes" in base_url else True
        async with httpx.AsyncClient(timeout=40.0, verify=verify) as client:
            resp = await client.get(
                lookup_url,
                params=lookup_params,
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Ensembl lookup に失敗しました: {resp.status_code} {resp.text}",
            )
        try:
            return resp.json()
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Ensembl lookup 応答の解析に失敗しました: {resp.text}",
            ) from exc

    local_gff_raw = os.getenv("LOCAL_GFF_PATH", "").strip()
    local_fa_raw = os.getenv("LOCAL_FASTA_PATH", "").strip()
    local_gff = Path(local_gff_raw).expanduser() if local_gff_raw else None
    local_fa = Path(local_fa_raw).expanduser() if local_fa_raw else None
    if local_gff_raw or local_fa_raw:
        if not local_gff or not local_fa or not local_gff.exists() or not local_fa.exists():
            raise HTTPException(status_code=400, detail="Configure both existing local GFF3 and FASTA files.")
        # A missing local gene is not permission to disclose its ID externally.
        return await run_in_threadpool(local_structure_from_gff, gene_id, species, local_gff, local_fa)

    def candidate_ids(original: str) -> list[str]:
        cands = [original]
        cands.append(re.sub(r"-T\\d+$", "", original))
        cands.append(re.sub(r"\\.\\d+$", "", original))
        return list(dict.fromkeys([c for c in cands if c]))

    async def xref_search(base_url: str, sid: str, sp: str | None) -> list[str]:
        verify = False if "ensemblgenomes" in base_url else True
        if not sp:
            return []
        urls = [
            f"{base_url}/xrefs/symbol/{sp}/{sid}",
            f"{base_url}/xrefs/name/{sp}/{sid}",
        ]
        ids: list[str] = []
        async with httpx.AsyncClient(timeout=20.0, verify=verify) as client:
            for u in urls:
                resp = await client.get(u, headers={"Accept": "application/json"})
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        for d in data:
                            if isinstance(d, dict):
                                val = d.get("id")
                                if val:
                                    ids.append(val)
                    except Exception:
                        continue
        return list(dict.fromkeys(ids))

    gene_json: dict[str, Any] | None = None
    used_base = base

    # メインの base（rest.ensembl.org など）でのみ試す
    for candidate_base in [base]:
        used_base = candidate_base
        # 候補IDで direct lookup
        for cid in candidate_ids(gene_id):
            try:
                gene_json = await _lookup(cid, candidate_base)
                gene_id = cid
                break
            except HTTPException:
                continue

        # 見つからないときは xrefs でシンボル検索
        if gene_json is None:
            for cid in candidate_ids(gene_id):
                ids = await xref_search(candidate_base, cid, species)
                for xid in ids:
                    try:
                        gene_json = await _lookup(xid, candidate_base)
                        gene_id = xid
                        break
                    except HTTPException:
                        continue
                if gene_json is not None:
                    break

        if gene_json is not None:
            break

    if gene_json is None:
        raise HTTPException(status_code=404, detail=f"Gene ID が見つかりませんでした: {gene_id}")

    try:
        seqid, start, end, strand, exons, cds = to_gene_structure(gene_json)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # エキソン/CDS 範囲は gene 座標上の値 (start/end) なので、配列取得時は gene 範囲を含む領域を取る
    # Ensembl の sequence/region API は 1-based inclusive
    region_start = min(start, end)
    region_end = max(start, end)

    seq_url = f"{used_base}/sequence/region/{gene_json.get('species') or species or ''}/{seqid}:{region_start}..{region_end}:{strand}"
    verify = False if "ensemblgenomes" in used_base else True
    async with httpx.AsyncClient(timeout=40.0, verify=verify) as client:
        resp = await client.get(seq_url, headers={"Accept": "text/plain"})
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Ensembl sequence 取得に失敗しました: {resp.status_code} {resp.text}",
        )
    seq = (resp.text or "").strip()
    if not seq:
        raise HTTPException(status_code=502, detail="Ensembl sequence が空です。")

    length = len(seq)

    def to_local_coords(r: dict[str, int]) -> dict[str, int]:
        # gene range start を 1 とする 1-based に正規化
        s = r["start"] - region_start + 1
        e = r["end"] - region_start + 1
        return {"start": min(s, e), "end": max(s, e)}

    return {
        "seq_region_name": seqid,
        "start": 1,
        "end": length,
        "strand": strand,
        "length": length,
        "sequence": seq,
        "exons": [to_local_coords(r) for r in exons],
        "cds": [to_local_coords(r) for r in cds],
        "source": "ensembl",
    }





