"""
Primer3（primer3_core）を利用したプライマー設計ロジックを提供するモジュール。

主な責務:
- primer3_core に渡す入力フォーマットの組み立て
- primer3_core の実行（subprocess）
- 出力テキストのパースと、API レスポンス用の dict への変換
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional

from .sequence_utils import normalize_sequence


@dataclass
class PrimerCandidate:
    """1 組のプライマー（left/right）とペア情報を表す内部モデル。"""

    index: int
    left_sequence: str
    right_sequence: str
    left_start: int
    left_length: int
    right_start: int
    right_length: int
    product_size: Optional[int]
    pair_penalty: Optional[float]
    left_tm: Optional[float]
    right_tm: Optional[float]
    left_gc_percent: Optional[float]
    right_gc_percent: Optional[float]


class Primer3NotFoundError(RuntimeError):
    """primer3_core 実行ファイルが見つからない場合のエラー。"""


class Primer3ExecutionError(RuntimeError):
    """primer3_core 実行時にエラーが発生した場合のエラー。"""


def _ensure_primer3_available(executable: str = "primer3_core") -> str:
    """
    primer3_core が PATH 上に存在するか確認し、パスを返す。

    見つからない場合は Primer3NotFoundError を送出する。
    """
    resolved = shutil.which(executable)
    if resolved is None:
        raise Primer3NotFoundError(
            "primer3_core が見つかりませんでした。Ubuntu / WSL 環境では、"
            "`sudo apt-get update && sudo apt-get install -y primer3` でインストールできます。"
        )
    return resolved


def _build_primer3_input(
    raw_sequence: str,
    num_return: int,
    product_size_range: Optional[str],
    target_start_1based: Optional[int],
    target_length: Optional[int],
    opt_tm: float,
    min_tm: float,
    max_tm: float,
    min_size: Optional[int] = None,
    opt_size: Optional[int] = None,
    max_size: Optional[int] = None,
    min_gc: Optional[float] = None,
    max_gc: Optional[float] = None,
    salt_monovalent: Optional[float] = None,
    dna_conc: Optional[float] = None,
) -> str:
    """
    primer3_core に渡す入力テキストを組み立てる。

    - raw_sequence はそのままではなく、空白除去＋大文字化した配列を利用する。
    - target_start_1based が指定されている場合、0-based に変換して SEQUENCE_TARGET を設定。
    - product_size_range は primer3 の形式（例: "100-300 400-600"）でそのまま渡す。
    """
    sequence = normalize_sequence(raw_sequence)

    # ベースラインのデフォルト値（Primer3PLUS に近い設定）
    min_size = min_size or 18
    opt_size = opt_size or 20
    max_size = max_size or 27
    min_gc = min_gc if min_gc is not None else 20.0
    max_gc = max_gc if max_gc is not None else 80.0
    salt_monovalent = salt_monovalent if salt_monovalent is not None else 50.0
    dna_conc = dna_conc if dna_conc is not None else 50.0

    lines = [
        "SEQUENCE_ID=bioapi_sequence",
        f"SEQUENCE_TEMPLATE={sequence}",
        "PRIMER_TASK=generic",
        "PRIMER_PICK_LEFT_PRIMER=1",
        "PRIMER_PICK_RIGHT_PRIMER=1",
        f"PRIMER_NUM_RETURN={num_return}",
        # サイズ・Tm の基本的なデフォルト値（必要に応じて上書きされる）
        f"PRIMER_MIN_SIZE={min_size}",
        f"PRIMER_OPT_SIZE={opt_size}",
        f"PRIMER_MAX_SIZE={max_size}",
        f"PRIMER_OPT_TM={opt_tm}",
        f"PRIMER_MIN_TM={min_tm}",
        f"PRIMER_MAX_TM={max_tm}",
        f"PRIMER_MIN_GC={min_gc}",
        f"PRIMER_MAX_GC={max_gc}",
        f"PRIMER_SALT_MONOVALENT={salt_monovalent}",
        f"PRIMER_DNA_CONC={dna_conc}",
    ]

    if product_size_range:
        lines.append(f"PRIMER_PRODUCT_SIZE_RANGE={product_size_range}")

    if target_start_1based is not None and target_length is not None:
        # primer3 は 0-based の start,length を要求する
        zero_based_start = max(target_start_1based - 1, 0)
        lines.append(f"SEQUENCE_TARGET={zero_based_start},{target_length}")

    lines.append("=")
    return "\n".join(lines) + "\n"


def _primer3_timeout_sec() -> float | None:
    """Return primer3_core subprocess timeout in seconds.

    Configurable via ``PRIMER3_TIMEOUT_SEC`` env var.
    ``0`` means *unlimited* (returns ``None``).
    Default is ``0`` (no limit) — the design intent is that every primer
    search must run to completion regardless of how long it takes.
    """
    raw = os.environ.get("PRIMER3_TIMEOUT_SEC", "0")
    try:
        val = int(raw)
    except (ValueError, TypeError):
        return None
    return None if val <= 0 else float(val)


def _run_primer3(input_text: str) -> str:
    """
    primer3_core をサブプロセスとして実行し、標準出力をテキストで返す。
    """
    executable = _ensure_primer3_available()
    timeout = _primer3_timeout_sec()

    try:
        proc = subprocess.run(
            [executable],
            input=input_text.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise Primer3ExecutionError(
            f"primer3_core がタイムアウトしました（{timeout}秒）。入力配列が長すぎる可能性があります。"
        ) from exc
    except OSError as exc:
        raise Primer3ExecutionError(f"primer3_core の実行に失敗しました: {exc}") from exc

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")
        raise Primer3ExecutionError(
            f"primer3_core がエラー終了しました（returncode={proc.returncode}）。\n"
            f"stderr:\n{stderr}"
        )

    return proc.stdout.decode("utf-8", errors="replace")


def _parse_primer3_output(output_text: str) -> List[PrimerCandidate]:
    """
    primer3_core の出力テキストをパースし、PrimerCandidate のリストに変換する。
    """
    left: Dict[int, Dict] = {}
    right: Dict[int, Dict] = {}
    pair: Dict[int, Dict] = {}

    for line in output_text.splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        # 左プライマー
        m = re.match(r"PRIMER_LEFT_(\d+)_SEQUENCE", key)
        if m:
            idx = int(m.group(1))
            left.setdefault(idx, {})["sequence"] = value
            continue

        m = re.match(r"PRIMER_LEFT_(\d+)$", key)
        if m:
            idx = int(m.group(1))
            start, length = value.split(",")
            left.setdefault(idx, {})
            left[idx]["start"] = int(start)
            left[idx]["length"] = int(length)
            continue

        m = re.match(r"PRIMER_LEFT_(\d+)_TM", key)
        if m:
            idx = int(m.group(1))
            left.setdefault(idx, {})["tm"] = float(value)
            continue

        m = re.match(r"PRIMER_LEFT_(\d+)_GC_PERCENT", key)
        if m:
            idx = int(m.group(1))
            left.setdefault(idx, {})["gc_percent"] = float(value)
            continue

        # 右プライマー
        m = re.match(r"PRIMER_RIGHT_(\d+)_SEQUENCE", key)
        if m:
            idx = int(m.group(1))
            right.setdefault(idx, {})["sequence"] = value
            continue

        m = re.match(r"PRIMER_RIGHT_(\d+)$", key)
        if m:
            idx = int(m.group(1))
            start, length = value.split(",")
            right.setdefault(idx, {})
            right[idx]["start"] = int(start)
            right[idx]["length"] = int(length)
            continue

        m = re.match(r"PRIMER_RIGHT_(\d+)_TM", key)
        if m:
            idx = int(m.group(1))
            right.setdefault(idx, {})["tm"] = float(value)
            continue

        m = re.match(r"PRIMER_RIGHT_(\d+)_GC_PERCENT", key)
        if m:
            idx = int(m.group(1))
            right.setdefault(idx, {})["gc_percent"] = float(value)
            continue

        # ペア情報
        m = re.match(r"PRIMER_PAIR_(\d+)_PRODUCT_SIZE", key)
        if m:
            idx = int(m.group(1))
            pair.setdefault(idx, {})["product_size"] = int(value)
            continue

        m = re.match(r"PRIMER_PAIR_(\d+)_PENALTY", key)
        if m:
            idx = int(m.group(1))
            pair.setdefault(idx, {})["penalty"] = float(value)
            continue

    candidates: List[PrimerCandidate] = []
    indices = sorted(set(left.keys()) & set(right.keys()))

    for idx in indices:
        l = left.get(idx, {})
        r = right.get(idx, {})
        p = pair.get(idx, {})

        if "sequence" not in l or "sequence" not in r:
            continue

        candidates.append(
            PrimerCandidate(
                index=idx,
                left_sequence=l["sequence"],
                right_sequence=r["sequence"],
                # primer3 の start は 0-based なので 1-based に変換して返す
                left_start=l.get("start", 0) + 1 if "start" in l else 0,
                left_length=l.get("length", 0),
                right_start=r.get("start", 0) + 1 if "start" in r else 0,
                right_length=r.get("length", 0),
                product_size=p.get("product_size"),
                pair_penalty=p.get("penalty"),
                left_tm=l.get("tm"),
                right_tm=r.get("tm"),
                left_gc_percent=l.get("gc_percent"),
                right_gc_percent=r.get("gc_percent"),
            )
        )

    return candidates


def design_primers(
    sequence: str,
    num_return: int = 5,
    product_size_range: Optional[str] = None,
    target_start_1based: Optional[int] = None,
    target_length: Optional[int] = None,
    opt_tm: float = 60.0,
    min_tm: float = 57.0,
    max_tm: float = 63.0,
    primer_min_size: Optional[int] = None,
    primer_opt_size: Optional[int] = None,
    primer_max_size: Optional[int] = None,
    primer_min_gc: Optional[float] = None,
    primer_max_gc: Optional[float] = None,
    primer_salt_monovalent: Optional[float] = None,
    primer_dna_conc: Optional[float] = None,
) -> List[Dict]:
    """
    primer3_core を用いてプライマー設計を行い、辞書のリストとして結果を返す。

    返り値の各要素は Pydantic モデルにそのまま渡せる形になっている。
    """
    if num_return <= 0:
        raise ValueError("num_return は 1 以上を指定してください。")

    input_text = _build_primer3_input(
        raw_sequence=sequence,
        num_return=num_return,
        product_size_range=product_size_range,
        target_start_1based=target_start_1based,
        target_length=target_length,
        opt_tm=opt_tm,
        min_tm=min_tm,
        max_tm=max_tm,
        min_size=primer_min_size,
        opt_size=primer_opt_size,
        max_size=primer_max_size,
        min_gc=primer_min_gc,
        max_gc=primer_max_gc,
        salt_monovalent=primer_salt_monovalent,
        dna_conc=primer_dna_conc,
    )

    output_text = _run_primer3(input_text)
    candidates = _parse_primer3_output(output_text)

    return [
        {
            "index": c.index,
            "left_sequence": c.left_sequence,
            "right_sequence": c.right_sequence,
            "left_start": c.left_start,
            "left_length": c.left_length,
            "right_start": c.right_start,
            "right_length": c.right_length,
            "product_size": c.product_size,
            "pair_penalty": c.pair_penalty,
            "left_tm": c.left_tm,
            "right_tm": c.right_tm,
            "left_gc_percent": c.left_gc_percent,
            "right_gc_percent": c.right_gc_percent,
        }
        for c in candidates
    ]
