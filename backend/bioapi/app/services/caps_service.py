"""
CAPS（Cleaved Amplified Polymorphic Sequences）マーカー設計サービス。

狙い:
- 指定したゲノム範囲（参照）から Primer3 で多数のプライマーペアを生成
- 比較ゲノム側は BLAST で対応領域を推定（または手動指定）
- 参照/比較の産物配列の制限酵素切断パターン差から共優勢候補を抽出
- ローカル BLAST DB で一意性（予測 PCR 産物数）も評価
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from Bio.Restriction import AllEnzymes, RestrictionBatch
from Bio.Seq import Seq

from . import blast_service, primer_service
from .job_service import JobCancelled
from .sequence_utils import normalize_sequence


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


# よく使う制限酵素（Biopython のクラス名）
DEFAULT_ENZYMES: list[str] = [
    # 6-cutters / 定番
    "EcoRI",
    "HindIII",
    "BamHI",
    "PstI",
    "XbaI",
    "SpeI",
    "XhoI",
    "SalI",
    "SacI",
    "KpnI",
    "ApaI",
    "NotI",
    "EcoRV",
    "BglII",
    "NcoI",
    "NdeI",
    # 4-cutters / CAPS でよく使う
    "HaeIII",
    "MspI",
    "RsaI",
    "AluI",
    "HinfI",
    "DdeI",
    "TaqI",
    "Sau3AI",
    "MboI",
    "AvaII",
    "BstUI",
    "BfaI",
    "NlaIII",
    "MseI",
    # 追加（実験で使われがち）
    "PvuII",
    "SmaI",
    "SspI",
    "DraI",
    "ClaI",
    "EcoO109I",
    "AflII",
    "AgeI",
    "BsrGI",
    "BsiWI",
    "BstEII",
    "NheI",
    "SbfI",
]


def _rev_comp(seq: str) -> str:
    s = normalize_sequence(seq)
    trans = str.maketrans({"A": "T", "T": "A", "C": "G", "G": "C"})
    return s.translate(trans)[::-1]


def _db_label(db_path: str) -> str:
    name = Path(db_path).name
    return name or db_path


def _subject_key(hit: blast_service.BlastHit) -> str:
    if hit.subject_chrom:
        return hit.subject_chrom
    return hit.sseqid.split()[0] if hit.sseqid else "-"


def _pick_preferred_gene(candidates: Iterable[str]) -> str | None:
    uniq = [c for c in dict.fromkeys([x for x in candidates if x])]
    return uniq[0] if uniq else None


def _compute_fragments(product_len: int, cut_positions: list[int]) -> list[int]:
    if product_len <= 0:
        return []
    # Biopython Restriction.search は「切断後の最初の塩基(1-based)」を返す。
    # 断片長を出すには、境界 = pos-1（= 先頭から何塩基目の後で切れるか）として扱う。
    boundaries = sorted({(p - 1) for p in cut_positions if 1 < p <= product_len})
    boundaries = [b for b in boundaries if 0 < b < product_len]
    if not boundaries:
        return [product_len]
    frags: list[int] = []
    prev = 0
    for b in boundaries:
        frags.append(b - prev)
        prev = b
    frags.append(product_len - prev)
    return frags


def _enzyme_classes(names: list[str]) -> tuple[list[type], list[str]]:
    available = {enzyme.__name__: enzyme for enzyme in AllEnzymes}
    valid: list[type] = []
    unknown: list[str] = []
    for name in names:
        n = (name or "").strip()
        if not n:
            continue
        cls = available.get(n)
        if cls is None:
            unknown.append(n)
            continue
        valid.append(cls)
    # 重複除去（順序は保持）
    seen: set[str] = set()
    unique_valid: list[type] = []
    for cls in valid:
        if cls.__name__ in seen:
            continue
        seen.add(cls.__name__)
        unique_valid.append(cls)
    return unique_valid, unknown


@dataclass(frozen=True)
class _PrimerBindingRange:
    start: int  # 1-based inclusive (template, plus orientation)
    end: int  # 1-based inclusive
    binding_seq: str  # on template (plus orientation)


def _infer_right_binding_range(
    template: str,
    right_primer: str,
    right_start: int,
    right_length: int,
    product_start: int,
    product_end: int,
) -> _PrimerBindingRange | None:
    """
    Primer3 の right_start/right_length が 5' 端/3' 端のどちら基準でも拾えるように、
    template 上の結合部（= reverse complement）範囲を推定する。
    """
    seq = normalize_sequence(template)
    bind = _rev_comp(right_primer)

    candidates: list[tuple[int, int]] = []
    # 右プライマーの start をそのまま左端とみなすケース
    candidates.append((right_start, right_start + right_length - 1))
    # 右プライマーの start を 3' 端とみなすケース（左側へ伸びる）
    candidates.append((right_start - right_length + 1, right_start))

    for s, e in candidates:
        if s < 1 or e > len(seq) or e < s:
            continue
        sub = seq[s - 1 : e]
        if sub == bind:
            return _PrimerBindingRange(start=s, end=e, binding_seq=bind)

    # 最後の手段: product 内で reverse complement を検索
    s0 = max(1, product_start)
    e0 = min(len(seq), product_end)
    window = seq[s0 - 1 : e0]
    idx = window.rfind(bind)
    if idx >= 0:
        s = s0 + idx
        e = s + len(bind) - 1
        return _PrimerBindingRange(start=s, end=e, binding_seq=bind)
    return None


def _is_contiguous_mapping(a_to_b: list[Optional[int]], a_start: int, length: int) -> bool:
    if length <= 0:
        return False
    base = a_to_b[a_start - 1] if 1 <= a_start <= len(a_to_b) else None
    if base is None:
        return False
    for i in range(length):
        a_pos = a_start + i
        if a_pos < 1 or a_pos > len(a_to_b):
            return False
        b = a_to_b[a_pos - 1]
        if b is None or b != base + i:
            return False
    return True


def _blast_align_map(query: str, subject: str) -> tuple[list[Optional[int]], float]:
    """
    blastn の gapped alignment を使って、query(参照)→subject(比較) の位置マップを作る。

    戻り値:
    - a_to_b: len(query) 要素。i(1-based) の対応先 subject 位置(1-based) or None
    - coverage: 対応が取れた塩基割合
    """
    q = normalize_sequence(query)
    s = normalize_sequence(subject)
    if not q or not s:
        return ([], 0.0)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=True) as qf, tempfile.NamedTemporaryFile(
        mode="w", suffix=".fa", delete=True
    ) as sf:
        qf.write(">q\n")
        qf.write(q + "\n")
        qf.flush()
        sf.write(">s\n")
        sf.write(s + "\n")
        sf.flush()

        cmd = [
            blast_service._blast_bin("blastn"),
            "-task",
            "megablast",
            "-query",
            qf.name,
            "-subject",
            sf.name,
            "-evalue",
            "1e-50",
            "-dust",
            "no",
            "-soft_masking",
            "false",
            "-outfmt",
            "6 qstart qend sstart send length pident qseq sseq",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise blast_service.BlastExecutionError(
                "blastn（query vs subject）の実行に失敗しました（returncode=%s）。\nstderr:\n%s"
                % (proc.returncode, proc.stderr)
            )

    hsps: list[tuple[int, int, int, int, str, str]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 8:
            continue
        qstart, qend, sstart, send = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
        qseq, sseq = parts[6], parts[7]
        hsps.append((qstart, qend, sstart, send, qseq, sseq))

    if not hsps:
        return ([None] * len(q), 0.0)

    hsps.sort(key=lambda x: (x[0], x[1]))
    a_to_b: list[Optional[int]] = [None] * len(q)
    mapped = 0

    for qstart, qend, sstart, send, qseq, sseq in hsps:
        qpos = qstart
        spos = sstart
        step = 1 if sstart <= send else -1
        for cq, cs in zip(qseq, sseq, strict=False):
            if cq != "-" and cs != "-":
                if 1 <= qpos <= len(a_to_b) and a_to_b[qpos - 1] is None:
                    a_to_b[qpos - 1] = spos
                    mapped += 1
                qpos += 1
                spos += step
            elif cq != "-" and cs == "-":
                qpos += 1
            elif cq == "-" and cs != "-":
                spos += step
            else:
                # gap-gap は通常出ない
                continue

    coverage = mapped / len(q) if q else 0.0
    return a_to_b, coverage


def _count_mismatches_in_range(
    seq_a: str,
    seq_b: str,
    a_to_b: list[Optional[int]],
    a_start: int,
    a_end: int,
) -> tuple[int, int | None]:
    a = normalize_sequence(seq_a)
    b = normalize_sequence(seq_b)
    mismatch = 0
    first_a_pos: int | None = None
    for a_pos in range(max(1, a_start), min(len(a), a_end) + 1):
        b_pos = a_to_b[a_pos - 1] if 1 <= a_pos <= len(a_to_b) else None
        if not b_pos:
            continue
        if b_pos < 1 or b_pos > len(b):
            continue
        ba = a[a_pos - 1]
        bb = b[b_pos - 1]
        if ba in {"A", "C", "G", "T"} and bb in {"A", "C", "G", "T"} and ba != bb:
            mismatch += 1
            if first_a_pos is None:
                first_a_pos = a_pos
    return mismatch, first_a_pos


def _blast_hits_to_amplicons(
    left_hits: list[blast_service.BlastHit],
    right_hits: list[blast_service.BlastHit],
    product_min: int,
    product_max: int,
) -> list[dict]:
    """
    左右プライマーの local BLAST ヒットから、形成しうる PCR 産物候補を列挙する。

    戻り値は dict のリスト:
    - subject, start, end, length, gene_label
    """
    if not left_hits or not right_hits:
        return []

    def to_geom(hit: blast_service.BlastHit) -> tuple[str, str, int, int]:
        # subject, strand, fivePrime, threePrime
        strand = "+" if hit.sstart <= hit.send else "-"
        s1 = hit.sstart
        s2 = hit.send
        five = min(s1, s2) if strand == "+" else max(s1, s2)
        three = max(s1, s2) if strand == "+" else min(s1, s2)
        return _subject_key(hit), strand, five, three

    left_geom = [(h, *to_geom(h)) for h in left_hits]
    right_geom = [(h, *to_geom(h)) for h in right_hits]

    amplicons: list[dict] = []
    seen: set[str] = set()

    for lh, subj_l, strand_l, five_l, three_l in left_geom:
        for rh, subj_r, strand_r, five_r, three_r in right_geom:
            if subj_l != subj_r:
                continue
            if strand_l == strand_r:
                continue
            start5 = min(five_l, five_r)
            end5 = max(five_l, five_r)
            inner_min3 = min(three_l, three_r)
            inner_max3 = max(three_l, three_r)
            # 5' の外側から 3' の内側へ向かう配置のみ
            if not (start5 <= inner_min3 and inner_max3 <= end5):
                continue
            length = end5 - start5 + 1
            if length < max(10, product_min) or length > product_max:
                continue
            key = f"{subj_l}|{start5}|{end5}"
            if key in seen:
                continue
            seen.add(key)
            amplicons.append(
                {
                    "subject": subj_l,
                    "start": start5,
                    "end": end5,
                    "length": length,
                }
            )

    if not amplicons:
        return []

    # gene ラベルを簡易付与（左右ヒットの gene_ids/names を寄せる）
    for amp in amplicons:
        gene_candidates: list[str] = []
        for h in [*left_hits, *right_hits]:
            if _subject_key(h) != amp["subject"]:
                continue
            hs = min(h.sstart, h.send)
            he = max(h.sstart, h.send)
            if he < amp["start"] or hs > amp["end"]:
                continue
            gene_candidates.extend(h.gene_names or [])
            gene_candidates.extend(h.gene_ids or [])
        amp["gene_label"] = _pick_preferred_gene(gene_candidates)

    # 代表は短い産物を優先
    amplicons.sort(key=lambda a: a["length"])
    return amplicons


def _quality_from_amplicons(n: int) -> str:
    if n == 1:
        return "S"
    if n == 0:
        return "D"
    if n == 2:
        return "C"
    if n == 3:
        return "B"
    return "D"


@dataclass(frozen=True)
class CapsDesignResult:
    ref_entry: str
    ref_start: int
    ref_end: int
    ref_seq: str
    alt_entry: str
    alt_start: int
    alt_end: int
    alt_strand: str
    alt_seq_oriented: str
    mapped_by_blast: bool
    a_to_b: list[Optional[int]]
    mapping_coverage: float


def _fetch_alt_region_by_blast(
    ref_seq: str,
    alt_db: str,
    ref_len: int,
    blast_num_threads: int | None,
) -> tuple[str, int, int, str, str]:
    """
    参照配列を比較 DB に投げて対応領域を推定し、配列を取得して返す。

    戻り値:
    - alt_entry, alt_start, alt_end, alt_strand, alt_seq_oriented
    """
    hits = blast_service.run_blastn_sync(
        sequence=ref_seq,
        db=alt_db,
        task="megablast",
        evalue=1e-20,
        max_target_seqs=5,
        num_threads=blast_num_threads,
        max_hsps=1,
    )
    if not hits:
        raise blast_service.BlastExecutionError("比較ゲノムへの BLAST でヒットが見つかりませんでした。")
    best = hits[0]
    if best.length < max(200, int(ref_len * 0.6)) or best.pident < 85.0:
        raise blast_service.BlastExecutionError(
            f"比較ゲノム側の対応領域推定が不安定です（len={best.length}, %id={best.pident}）。範囲を狭める/手動指定を試してください。"
        )

    entry = best.sseqid.split()[0]
    s_left = min(best.sstart, best.send)
    s_right = max(best.sstart, best.send)
    strand = "plus" if best.sstart <= best.send else "minus"

    # 参照長に寄せるように少し広めに取る
    hit_len = s_right - s_left + 1
    missing = max(0, ref_len - hit_len)
    margin = 250
    pad_left = margin + missing // 2
    pad_right = margin + (missing - missing // 2)

    fetch_start = max(1, s_left - pad_left)
    fetch_end = s_right + pad_right
    seq = blast_service.fetch_sequence_local_db(
        alt_db,
        entry,
        fetch_start,
        fetch_end,
        strand="plus",
        max_len=250_000,
    )
    oriented = _rev_comp(seq) if strand == "minus" else seq
    return entry, fetch_start, fetch_end, strand, oriented


def prepare_caps_design(
    *,
    ref_db: str,
    ref_entry: str,
    ref_start: int,
    ref_end: int,
    alt_db: str,
    map_alt_by_blast: bool,
    alt_entry: str | None,
    alt_start: int | None,
    alt_end: int | None,
    alt_strand: str,
    blast_num_threads: int | None,
) -> CapsDesignResult:
    ref_seq = blast_service.fetch_sequence_local_db(
        ref_db,
        ref_entry,
        ref_start,
        ref_end,
        strand="plus",
        max_len=250_000,
    )
    ref_seq = normalize_sequence(ref_seq)
    if not ref_seq:
        raise blast_service.BlastExecutionError("参照配列を取得できませんでした。")

    mapped_by_blast = False
    if map_alt_by_blast:
        mapped_by_blast = True
        entry, astart, aend, strand, alt_oriented = _fetch_alt_region_by_blast(
            ref_seq=ref_seq,
            alt_db=alt_db,
            ref_len=len(ref_seq),
            blast_num_threads=blast_num_threads,
        )
    else:
        if not alt_entry or alt_start is None or alt_end is None:
            raise blast_service.BlastExecutionError("比較ゲノムの entry/start/end を指定してください。")
        strand_norm = (alt_strand or "plus").lower()
        if strand_norm not in {"plus", "minus"}:
            raise blast_service.BlastExecutionError('alt_strand は "plus"/"minus" のいずれかです。')
        entry = alt_entry
        astart = min(alt_start, alt_end)
        aend = max(alt_start, alt_end)
        seq = blast_service.fetch_sequence_local_db(
            alt_db,
            entry,
            astart,
            aend,
            strand="plus",
            max_len=250_000,
        )
        alt_oriented = _rev_comp(seq) if strand_norm == "minus" else seq
        strand = strand_norm

    alt_oriented = normalize_sequence(alt_oriented)
    if not alt_oriented:
        raise blast_service.BlastExecutionError("比較配列を取得できませんでした。")

    a_to_b, cov = _blast_align_map(ref_seq, alt_oriented)
    if not a_to_b:
        raise blast_service.BlastExecutionError("参照/比較のアラインメントに失敗しました。")
    if cov < 0.55:
        raise blast_service.BlastExecutionError(
            f"参照/比較の対応付けが不十分です（coverage={cov:.2f}）。範囲を狭めるか、比較側を手動指定してください。"
        )

    return CapsDesignResult(
        ref_entry=ref_entry,
        ref_start=ref_start,
        ref_end=ref_end,
        ref_seq=ref_seq,
        alt_entry=entry,
        alt_start=astart,
        alt_end=aend,
        alt_strand=strand,
        alt_seq_oriented=alt_oriented,
        mapped_by_blast=mapped_by_blast,
        a_to_b=a_to_b,
        mapping_coverage=cov,
    )


def design_caps_markers(
    *,
    ref_db: str,
    ref_entry: str,
    ref_start: int,
    ref_end: int,
    alt_db: str,
    map_alt_by_blast: bool,
    alt_entry: str | None,
    alt_start: int | None,
    alt_end: int | None,
    alt_strand: str,
    product_min: int,
    product_max: int,
    primer_num_return: int,
    max_markers: int,
    enzymes: list[str],
    enzymes_per_primer: int,
    max_cuts_per_allele: int,
    min_fragment_len: int,
    require_perfect_primers_in_alt: bool,
    blast_check_dbs: list[str],
    blast_num_threads: int | None,
    blast_max_target_seqs: int,
    opt_tm: float,
    min_tm: float,
    max_tm: float,
    primer_min_size: int | None,
    primer_opt_size: int | None,
    primer_max_size: int | None,
    primer_min_gc: float | None,
    primer_max_gc: float | None,
    primer_salt_monovalent: float | None,
    primer_dna_conc: float | None,
    progress_cb: Callable[[float, str | None], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> dict:
    """
    CAPS 候補一覧を作る（同期処理）。

    返り値は API のレスポンスにそのまま乗せられる dict。
    """
    def report(p: float, msg: str | None = None) -> None:
        if progress_cb:
            progress_cb(p, msg)

    def check_cancel() -> None:
        if cancel_cb and cancel_cb():
            raise JobCancelled("ジョブがキャンセルされました。")

    if product_min > product_max:
        raise ValueError("product_min は product_max 以下を指定してください。")
    if ref_end < ref_start:
        raise ValueError("ref_end は ref_start 以上を指定してください。")
    ref_span = ref_end - ref_start + 1
    max_region_bp = _env_int("CAPS_MAX_REGION_BP", 120_000)
    if ref_span > max_region_bp:
        raise ValueError(
            f"参照領域が大きすぎます（{ref_span}bp > {max_region_bp}bp）。"
            "範囲を絞って実行してください。"
        )
    max_primer_num_return = _env_int("CAPS_MAX_PRIMER_NUM_RETURN", 800)
    if primer_num_return > max_primer_num_return:
        raise ValueError(
            f"primer_num_return が上限を超えています（{primer_num_return} > {max_primer_num_return}）。"
        )
    max_marker_rows = _env_int("CAPS_MAX_MARKERS", 1200)
    if max_markers > max_marker_rows:
        raise ValueError(f"max_markers が上限を超えています（{max_markers} > {max_marker_rows}）。")

    warnings: list[str] = []

    report(0.02, "prepare (fetch/align)")
    check_cancel()
    prep = prepare_caps_design(
        ref_db=ref_db,
        ref_entry=ref_entry,
        ref_start=ref_start,
        ref_end=ref_end,
        alt_db=alt_db,
        map_alt_by_blast=map_alt_by_blast,
        alt_entry=alt_entry,
        alt_start=alt_start,
        alt_end=alt_end,
        alt_strand=alt_strand,
        blast_num_threads=blast_num_threads,
    )

    report(0.12, "digest setup")
    enzyme_names = enzymes or DEFAULT_ENZYMES
    enzyme_classes, unknown = _enzyme_classes(enzyme_names)
    if unknown:
        warnings.append(f"未知の制限酵素名を無視しました: {', '.join(unknown[:20])}{'...' if len(unknown) > 20 else ''}")
    if not enzyme_classes:
        raise ValueError("利用可能な制限酵素が 1 つもありません。")

    batch = RestrictionBatch(enzyme_classes)

    product_range = f"{product_min}-{product_max}"

    report(0.18, "Primer3 (generate pairs)")
    check_cancel()
    primer_pairs = primer_service.design_primers(
        sequence=prep.ref_seq,
        num_return=primer_num_return,
        product_size_range=product_range,
        target_start_1based=None,
        target_length=None,
        opt_tm=opt_tm,
        min_tm=min_tm,
        max_tm=max_tm,
        primer_min_size=primer_min_size,
        primer_opt_size=primer_opt_size,
        primer_max_size=primer_max_size,
        primer_min_gc=primer_min_gc,
        primer_max_gc=primer_max_gc,
        primer_salt_monovalent=primer_salt_monovalent,
        primer_dna_conc=primer_dna_conc,
    )

    # marker rows: (primer_left, primer_right, enzyme-specific fields...)
    rows: list[dict] = []

    report(0.28, f"scan primer pairs (n={len(primer_pairs)})")
    for pair in primer_pairs:
        # Primer3 の index は 0-based のことが多いが、進捗用途なので厳密でなくてよい
        try:
            idx = int(pair.get("index") or 0)
        except (TypeError, ValueError):
            idx = 0
        if len(primer_pairs) >= 50 and idx % 10 == 0:
            report(0.28 + 0.47 * min(1.0, idx / max(1, len(primer_pairs))), None)
        check_cancel()
        if len(rows) >= max_markers:
            break

        left_seq = normalize_sequence(pair.get("left_sequence", ""))
        right_seq = normalize_sequence(pair.get("right_sequence", ""))
        if not left_seq or not right_seq:
            continue
        left_start = int(pair.get("left_start") or 0)
        left_len = int(pair.get("left_length") or 0)
        right_start = int(pair.get("right_start") or 0)
        right_len = int(pair.get("right_length") or 0)
        product_size = int(pair.get("product_size") or 0)

        if left_start <= 0 or right_start <= 0 or left_len <= 0 or right_len <= 0:
            continue
        amp_start = max(1, min(left_start, right_start))
        amp_end = amp_start + product_size - 1 if product_size > 0 else min(len(prep.ref_seq), max(left_start + left_len - 1, right_start + right_len - 1))
        if amp_end > len(prep.ref_seq) or amp_end <= amp_start:
            continue

        # alt 側の対応（boundary が取れないものは除外）
        b_start = prep.a_to_b[amp_start - 1] if 1 <= amp_start <= len(prep.a_to_b) else None
        b_end = prep.a_to_b[amp_end - 1] if 1 <= amp_end <= len(prep.a_to_b) else None
        if not b_start or not b_end or b_start <= 0 or b_end <= 0:
            continue
        if b_end < b_start:
            continue
        if b_end > len(prep.alt_seq_oriented):
            continue

        # primer binding site の完全一致チェック（比較側）
        right_bind = _infer_right_binding_range(
            template=prep.ref_seq,
            right_primer=right_seq,
            right_start=right_start,
            right_length=right_len,
            product_start=amp_start,
            product_end=amp_end,
        )
        if right_bind is None:
            continue

        if require_perfect_primers_in_alt:
            if not _is_contiguous_mapping(prep.a_to_b, left_start, left_len):
                continue
            if not _is_contiguous_mapping(prep.a_to_b, right_bind.start, right_len):
                continue
            left_b = prep.a_to_b[left_start - 1] or 0
            right_b = prep.a_to_b[right_bind.start - 1] or 0
            if left_b <= 0 or right_b <= 0:
                continue
            if left_b - 1 + left_len > len(prep.alt_seq_oriented):
                continue
            if right_b - 1 + right_len > len(prep.alt_seq_oriented):
                continue
            if prep.alt_seq_oriented[left_b - 1 : left_b - 1 + left_len] != left_seq:
                continue
            if prep.alt_seq_oriented[right_b - 1 : right_b - 1 + right_len] != right_bind.binding_seq:
                continue

        product_a = prep.ref_seq[amp_start - 1 : amp_end]
        product_b = prep.alt_seq_oriented[b_start - 1 : b_end]

        mismatch_count, _first = _count_mismatches_in_range(
            prep.ref_seq,
            prep.alt_seq_oriented,
            prep.a_to_b,
            amp_start,
            amp_end,
        )
        # mismatch 0 でも、indel によって CAPS になるケースがあるため length 差は許容する
        if mismatch_count <= 0 and len(product_a) == len(product_b):
            continue

        # digest pattern
        ana_a = batch.search(Seq(product_a))
        ana_b = batch.search(Seq(product_b))

        enzyme_candidates: list[dict] = []
        for enz in enzyme_classes:
            cuts_a = sorted({int(p) for p in ana_a.get(enz, [])})
            cuts_b = sorted({int(p) for p in ana_b.get(enz, [])})
            if max_cuts_per_allele >= 0:
                if len(cuts_a) > max_cuts_per_allele or len(cuts_b) > max_cuts_per_allele:
                    continue
            fr_a = _compute_fragments(len(product_a), cuts_a)
            fr_b = _compute_fragments(len(product_b), cuts_b)
            if fr_a == fr_b:
                continue
            if min(fr_a) < min_fragment_len or min(fr_b) < min_fragment_len:
                continue

            one_vs_zero = (len(cuts_a) > 0) != (len(cuts_b) > 0)
            score = 100 if one_vs_zero else 60
            score -= (len(cuts_a) + len(cuts_b)) * 3
            score += min(min(fr_a), min(fr_b)) / 10.0

            enzyme_candidates.append(
                {
                    "enzyme": enz.__name__,
                    "cuts_ref": cuts_a,
                    "cuts_alt": cuts_b,
                    "fragments_ref": fr_a,
                    "fragments_alt": fr_b,
                    "_score": score,
                }
            )

        if not enzyme_candidates:
            continue

        enzyme_candidates.sort(key=lambda x: x["_score"], reverse=True)
        enzyme_candidates = enzyme_candidates[: max(1, enzymes_per_primer)]

        # alt genome 座標（oriented index → genome coord）
        def alt_coord(pos_oriented_1based: int) -> int:
            if prep.alt_strand == "plus":
                return prep.alt_start + pos_oriented_1based - 1
            return prep.alt_end - pos_oriented_1based + 1

        alt_g0 = alt_coord(b_start)
        alt_g1 = alt_coord(b_end)
        alt_product_start = min(alt_g0, alt_g1)
        alt_product_end = max(alt_g0, alt_g1)

        ref_product_start = prep.ref_start + amp_start - 1
        ref_product_end = prep.ref_start + amp_end - 1

        gene_label = None
        # gene 候補は primer BLAST 側で付与するが、まずは ref/alt 側の HSP が無いので空

        for cand in enzyme_candidates:
            if len(rows) >= max_markers:
                break
            rows.append(
                {
                    "enzyme": cand["enzyme"],
                    "primer_left": left_seq,
                    "primer_right": right_seq,
                    "product_len_ref": len(product_a),
                    "product_len_alt": len(product_b),
                    "ref_product_start": ref_product_start,
                    "ref_product_end": ref_product_end,
                    "alt_product_start": alt_product_start,
                    "alt_product_end": alt_product_end,
                    "alt_strand": prep.alt_strand,
                    "mismatch_count": mismatch_count,
                    "cuts_ref": cand["cuts_ref"],
                    "cuts_alt": cand["cuts_alt"],
                    "fragments_ref": cand["fragments_ref"],
                    "fragments_alt": cand["fragments_alt"],
                    "gene_label": gene_label,
                    "blast": [],
                }
            )
    # ここから BLAST 一意性チェック（最終 rows の primer だけ）
    if not rows:
        report(1.0, "done (no markers)")
        return {
            "ref_db": ref_db,
            "ref_entry": ref_entry,
            "ref_start": ref_start,
            "ref_end": ref_end,
            "ref_length": len(prep.ref_seq),
            "alt_db": alt_db,
            "alt_entry": prep.alt_entry,
            "alt_start": prep.alt_start,
            "alt_end": prep.alt_end,
            "alt_strand": prep.alt_strand,
            "alt_length": len(prep.alt_seq_oriented),
            "mapped_by_blast": prep.mapped_by_blast,
            "primer_pairs_generated": len(primer_pairs),
            "markers": [],
            "warnings": warnings,
        }

    check_dbs = blast_check_dbs[:] if blast_check_dbs else [ref_db]
    # 重複除去
    check_dbs = [d for d in dict.fromkeys([d for d in check_dbs if d])]
    max_check_dbs = _env_int("CAPS_MAX_CHECK_DBS", 6)
    if len(check_dbs) > max_check_dbs:
        raise ValueError(
            f"blast_check_dbs が多すぎます（{len(check_dbs)} > {max_check_dbs}）。"
            "DB数を減らしてください。"
        )

    # primer sequence をユニーク化して batch blast
    primer_list: list[str] = []
    idx_by_seq: dict[str, int] = {}
    for r in rows:
        for key in ("primer_left", "primer_right"):
            s = r[key]
            if s in idx_by_seq:
                continue
            idx_by_seq[s] = len(primer_list)
            primer_list.append(s)
    max_primer_blast_queries = _env_int("CAPS_MAX_PRIMER_BLAST_QUERIES", 1600)
    if len(primer_list) > max_primer_blast_queries:
        raise ValueError(
            f"内部 primer BLAST クエリ数が上限を超えています（{len(primer_list)} > {max_primer_blast_queries}）。"
            "max_markers / primer_num_return を下げてください。"
        )

    # 複数 DB の primer BLAST は並列化する（DBごとに別プロセスを起動するため効果が大きい）
    blast_cache: dict[tuple[str, int], list[blast_service.BlastHit]] = {}
    max_workers = min(len(check_dbs), _env_int("CAPS_BLAST_MAX_PARALLEL_DBS", 2))
    threads = blast_service.clamp_blast_threads(blast_num_threads, parallel_jobs=max_workers)
    report(0.78, f"local primer BLAST (dbs={len(check_dbs)}, queries={len(primer_list)})")
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(
                blast_service.run_blastn_batch_local,
                primer_list,
                db,
                "blastn-short",
                1000,
                blast_max_target_seqs,
                threads,
                1,
            ): db
            for db in check_dbs
        }
        done = 0
        for fut in as_completed(futures):
            check_cancel()
            db = futures[fut]
            try:
                hits_per_query = fut.result()
            except blast_service.BlastExecutionError as exc:
                raise blast_service.BlastExecutionError(f"{_db_label(db)}: {exc}") from exc
            for i, hits in enumerate(hits_per_query):
                blast_cache[(db, i)] = hits
            done += 1
            report(0.78 + 0.17 * (done / max(1, len(check_dbs))), f"primer BLAST: {done}/{len(check_dbs)}")

    for i, r in enumerate(rows, start=1):
        if i % 25 == 0:
            report(0.95, "assembling results")
        check_cancel()
        left_idx = idx_by_seq.get(r["primer_left"])
        right_idx = idx_by_seq.get(r["primer_right"])
        per_db: list[dict] = []
        gene_labels: list[str] = []
        for db in check_dbs:
            lh = blast_cache.get((db, left_idx), []) if left_idx is not None else []
            rh = blast_cache.get((db, right_idx), []) if right_idx is not None else []
            amplicons = _blast_hits_to_amplicons(lh, rh, product_min, product_max)
            count = len(amplicons)
            top = amplicons[0] if amplicons else None
            gene = top.get("gene_label") if top else None
            if gene:
                gene_labels.append(gene)
            per_db.append(
                {
                    "db": _db_label(db),
                    "amplicon_count": count,
                    "quality": _quality_from_amplicons(count),
                    "top_subject": top.get("subject") if top else None,
                    "top_start": top.get("start") if top else None,
                    "top_end": top.get("end") if top else None,
                    "gene_label": gene,
                }
            )
        r["index"] = i
        r["blast"] = per_db
        if not r.get("gene_label"):
            r["gene_label"] = _pick_preferred_gene(gene_labels)

    report(1.0, "done")
    logger.info(
        "caps design finished ref=%s:%s-%s alt=%s mapped=%s primers=%s markers=%s coverage=%.3f",
        ref_entry,
        ref_start,
        ref_end,
        prep.alt_entry,
        prep.mapped_by_blast,
        len(primer_pairs),
        len(rows),
        prep.mapping_coverage,
    )

    return {
        "ref_db": ref_db,
        "ref_entry": ref_entry,
        "ref_start": ref_start,
        "ref_end": ref_end,
        "ref_length": len(prep.ref_seq),
        "alt_db": alt_db,
        "alt_entry": prep.alt_entry,
        "alt_start": prep.alt_start,
        "alt_end": prep.alt_end,
        "alt_strand": prep.alt_strand,
        "alt_length": len(prep.alt_seq_oriented),
        "mapped_by_blast": prep.mapped_by_blast,
        "primer_pairs_generated": len(primer_pairs),
        "markers": rows,
        "warnings": warnings,
    }

