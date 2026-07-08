"""
外部注釈 API（Ensembl REST / UniProt）を呼び出すためのサービスモジュール。

最初の段階では、最小限の情報のみを取得する。
"""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx

from ..core.config import get_settings


class AnnotFetchError(RuntimeError):
    """注釈 API 呼び出し時のエラー。"""


UNIPROT_REST_BASE = "https://rest.uniprot.org"

# Ensembl Gene ID / UniProt accession に許可する文字パターン（URL 挿入防止）
_VALID_ID_RE = re.compile(r"^[A-Za-z0-9_.\-:]+$")


def _validate_identifier(value: str, label: str) -> str:
    """Validate that an identifier contains only safe characters for URL interpolation."""
    s = (value or "").strip()
    if not s:
        raise AnnotFetchError(f"{label} が空です。")
    if len(s) > 200:
        raise AnnotFetchError(f"{label} が長すぎます（最大200文字）。")
    if not _VALID_ID_RE.match(s):
        raise AnnotFetchError(
            f"{label} に使用できない文字が含まれています: {s!r}"
        )
    return s


async def fetch_ensembl_gene(gene_id: str, species: Optional[str] = None) -> dict[str, Any]:
    """
    Ensembl REST API を用いて gene 情報を取得する。

    - gene_id: Ensembl Gene ID（例: ENSG000... / AT1G01010 など）
    - species: 必要に応じて明示的な species 名を指定可能（未使用でもよい）
    """
    gene_id = _validate_identifier(gene_id, "gene_id")
    settings = get_settings()
    base = settings.ensembl_rest_base_url.rstrip("/")
    url = f"{base}/lookup/id/{gene_id}"
    params = {"content-type": "application/json"}
    if species:
        params["species"] = species

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            url,
            params=params,
            headers={"Accept": "application/json"},
        )

    if resp.status_code != 200:
        raise AnnotFetchError(
            f"Ensembl REST から gene 情報を取得できませんでした "
            f"(status={resp.status_code}, body={resp.text[:200]}...)"
        )

    data = resp.json()
    # 必要な最小限の項目だけ返す
    return {
        "id": data.get("id"),
        "display_name": data.get("display_name"),
        "biotype": data.get("biotype"),
        "species": data.get("species"),
        "start": data.get("start"),
        "end": data.get("end"),
        "strand": data.get("strand"),
        "seq_region_name": data.get("seq_region_name"),
        "source": data.get("source"),
    }


async def fetch_uniprot_protein(accession: str) -> dict[str, Any]:
    """
    UniProt REST API を用いてタンパク質注釈を取得する。

    - accession: UniProt アクセッション（例: P12345）
    """
    accession = _validate_identifier(accession, "accession")
    url = f"{UNIPROT_REST_BASE}/uniprotkb/{accession}.json"

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url)

    if resp.status_code != 200:
        raise AnnotFetchError(
            f"UniProt REST からタンパク質情報を取得できませんでした "
            f"(status={resp.status_code}, body={resp.text[:200]}...)"
        )

    data = resp.json()

    protein_desc = (
        (data.get("proteinDescription") or {})
        .get("recommendedName", {})
        .get("fullName", {})
        .get("value")
    )

    gene_names: list[str] = []
    for gene in data.get("genes", []) or []:
        if not isinstance(gene, dict):
            continue
        name_val = (gene.get("geneName") or {}).get("value")
        if isinstance(name_val, str) and name_val.strip():
            gene_names.append(name_val.strip())
        for syn in gene.get("synonyms", []) or []:
            if isinstance(syn, dict):
                v = syn.get("value")
                if isinstance(v, str) and v.strip():
                    gene_names.append(v.strip())

    # De-duplicate (keep order)
    gene_names = list(dict.fromkeys(gene_names))

    go_terms: list[str] = []
    # New schema: uniProtKBCrossReferences. Legacy: dbReferences.
    crossrefs = data.get("uniProtKBCrossReferences") or data.get("dbReferences") or []
    for ref in crossrefs:
        if not isinstance(ref, dict):
            continue
        db = ref.get("database") or ref.get("type")
        if db != "GO":
            continue
        go_id = ref.get("id")
        if isinstance(go_id, str) and go_id.strip():
            go_terms.append(go_id.strip())
    go_terms = list(dict.fromkeys(go_terms))

    return {
        "accession": (data.get("primaryAccession") or accession),
        "protein_name": protein_desc,
        "gene_names": gene_names,
        "organism": (data.get("organism") or {}).get("scientificName"),
        "length": data.get("sequence", {}).get("length"),
        "go_terms": go_terms,
    }
