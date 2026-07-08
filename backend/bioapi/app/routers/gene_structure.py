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
from Bio.Seq import Seq
from Bio.SeqIO.FastaIO import SimpleFastaParser
from fastapi import APIRouter, HTTPException, Query

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


def load_local_sequence(fasta_path: Path, seqid: str) -> str:
    """
    FASTA (.fa or .fa.gz) から seqid の配列を読み出す（改行を除去して返す）。
    """
    if not fasta_path.exists():
        raise HTTPException(status_code=500, detail=f"FASTA が見つかりません: {fasta_path}")

    # SimpleFastaParser は file object を要求するため、gzip/通常の両方に対応する
    opener = gzip.open if fasta_path.suffix.endswith("gz") else open
    with opener(fasta_path, "rt") as fh:
        for header, seq in SimpleFastaParser(fh):
            header_id = header.split()[0]
            if header_id == seqid:
                return seq.strip()

    raise HTTPException(status_code=404, detail=f"FASTA に {seqid} が見つかりませんでした。")


def parse_attrs(attr_str: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for field in attr_str.split(";"):
        if not field:
            continue
        if "=" in field:
            k, v = field.split("=", 1)
            attrs[k.strip()] = v.strip()
    return attrs


def local_structure_from_gff(
    gene_id: str,
    species: str | None,
    gff_path: Path,
    fasta_path: Path,
) -> dict[str, Any]:
    if not gff_path.exists():
        raise HTTPException(status_code=500, detail=f"GFF3 が見つかりません: {gff_path}")

    target = gene_id.split("-")[0].split(".")[0]
    exons: list[dict[str, int]] = []
    cds_list: list[dict[str, int]] = []
    seqid: str | None = None
    strand: int = 1
    gene_start: int | None = None
    gene_end: int | None = None

    with _open_text_maybe_gzip(gff_path) as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            seqid_line, _source, ftype, start_s, end_s, _score, strand_s, _phase, attrs_s = parts
            if ftype not in {"gene", "mRNA", "transcript", "exon", "CDS"}:
                continue
            attrs = parse_attrs(attrs_s)
            candidates = [
                attrs.get("ID"),
                attrs.get("Parent"),
                attrs.get("gene_id"),
                attrs.get("gene"),
                attrs.get("Name"),
            ]
            if not any((c and target in c) for c in candidates):
                continue

            try:
                start = int(start_s)
                end = int(end_s)
            except ValueError:
                continue

            seqid = seqid_line
            strand = 1 if strand_s == "+" else -1
            gene_start = start if gene_start is None else min(gene_start, start)
            gene_end = end if gene_end is None else max(gene_end, end)
            if ftype == "exon":
                exons.append({"start": start, "end": end})
            if ftype == "CDS":
                cds_list.append({"start": start, "end": end})

    if not exons and not cds_list:
        raise HTTPException(status_code=404, detail=f"GFF3 に {gene_id} を含むエントリが見つかりませんでした。")
    if seqid is None or gene_start is None or gene_end is None:
        raise HTTPException(status_code=404, detail="GFF3 から座標を解釈できませんでした。")

    # 配列取得
    seq = load_local_sequence(fasta_path, seqid)
    region_seq = seq[gene_start - 1 : gene_end]
    if strand == -1:
        region_seq = str(Seq(region_seq).reverse_complement())

    length = len(region_seq)

    def norm(r: dict[str, int]) -> dict[str, int]:
        # gene start基準 1-based に直し、逆鎖なら反転
        if strand == 1:
            return {"start": r["start"] - gene_start + 1, "end": r["end"] - gene_start + 1}
        new_start = length - (r["end"] - gene_start + 1) + 1
        new_end = length - (r["start"] - gene_start + 1) + 1
        return {"start": min(new_start, new_end), "end": max(new_start, new_end)}

    return {
        "seq_region_name": seqid,
        "start": 1,
        "end": length,
        "strand": strand,
        "length": length,
        "sequence": region_seq,
        "exons": [norm(r) for r in exons],
        "cds": [norm(r) for r in cds_list],
        "source": "local",
    }


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
    if local_gff and local_fa and local_gff.exists() and local_fa.exists():
        try:
            return local_structure_from_gff(gene_id, species, local_gff, local_fa)
        except HTTPException:
            # ローカルで見つからない場合のみ Ensembl REST を試す
            pass

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





