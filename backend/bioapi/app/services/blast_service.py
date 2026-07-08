"""
BLAST+（blastn など）を CLI 経由で実行するためのラッパーモジュール。

最初はシンプルに、同期的に BLAST を叩いて TSV 形式の結果を返す。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import hashlib
import json
import os
import logging
import random
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import statistics
import defusedxml.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
import gzip
from multiprocessing import cpu_count
from pathlib import Path
from typing import Iterable, List

import httpx

from ..core.config import get_settings
from ..core.paths import blast_cold_databases_dir, blast_databases_dir, blast_gpu_prefilter_cache_dir, workbench_tmp_dir, expand_user_path


class BlastExecutionError(RuntimeError):
    """BLAST 実行時にエラーが発生した場合のエラー。"""


class BlastInputError(BlastExecutionError):
    """ユーザー入力（座標/entry/strand 等）の不正。"""


class BlastNotFoundError(BlastExecutionError):
    """DB や entry が見つからない場合のエラー。"""


# 個人利用で「速い」を優先しつつ暴走を避けるため、BLAST の num_threads は上限を設ける。
# （CPU 24 threads の環境を想定）
_BLAST_THREADS_CAP = 24
_GPU_PREFILTER_LOCK = threading.Lock()
_CUDA_QUARANTINE_LOCK = threading.Lock()
_CUDA_WORKLOAD_QUARANTINE: dict[str, tuple[float, str]] = {}


def _chrom_alias_cache_dir() -> Path:
    return workbench_tmp_dir() / "chrom_aliases"


def _chrom_alias_cache_path(db: str, ref_db: str) -> Path:
    db_norm = normalize_db_prefix(db)
    ref_norm = normalize_db_prefix(ref_db)
    db_name = Path(db_norm).name
    ref_name = Path(ref_norm).name
    h = hashlib.sha256(f"{db_norm}__{ref_norm}".encode("utf-8")).hexdigest()[:10]
    return _chrom_alias_cache_dir() / f"{db_name}__{h}__to__{ref_name}.json"


def _load_chrom_alias_override(db: str) -> dict[str, object] | None:
    """
    直近の推定結果（どの ref_db でも良い）を読み込む。
    """
    db_norm = normalize_db_prefix(db)
    db_name = Path(db_norm).name
    cache_dir = _chrom_alias_cache_dir()
    if not cache_dir.exists():
        return None
    candidates = sorted(cache_dir.glob(f"{db_name}__*__to__*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in candidates[:3]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("mapping"), dict):
                return data
        except Exception:
            continue
    return None


def _load_chrom_alias_override_for_ref(db: str, ref_db: str) -> dict[str, object] | None:
    p = _chrom_alias_cache_path(db, ref_db)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("mapping"), dict):
            return data
    except Exception:
        return None
    return None


def _invalidate_local_db_caches() -> None:
    # lru_cache をまとめて無効化（推定結果がすぐ反映されるように）
    try:
        _load_entry_alias_map.cache_clear()  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        _load_db_entry_catalog.cache_clear()  # type: ignore[attr-defined]
    except Exception:
        pass


def normalize_db_prefix(db: str) -> str:
    """
    Normalize a user-provided local BLAST DB prefix.

    - Expands "~" / "$VARS"
    - If a bare name (no slashes) is provided, resolve it under BLAST_DATABASES_DIR.
      (e.g. "reference_v2" -> "~/sequence_workbench/blast_databases/reference_v2")
    """
    s = expand_user_path(db)
    if not s:
        return s
    if "/" not in s and "\\" not in s:
        return str(blast_databases_dir() / s)
    return s


def _resolve_suffix_db_candidate(db_prefix: str, suffix: str) -> tuple[str, bool]:
    """
    Resolve `db_prefix + suffix`, optionally from a secondary cold-data root.

    Returns: (candidate_path, from_cold_root)
    """
    db_norm = normalize_db_prefix(db_prefix)
    base = Path(db_norm)
    local_candidate = Path(f"{db_norm}{suffix}")
    if _db_exists(local_candidate):
        return str(local_candidate), False

    cold_root = blast_cold_databases_dir()
    if cold_root:
        cold_candidate = cold_root / f"{base.name}{suffix}"
        if _db_exists(cold_candidate):
            return str(cold_candidate), True

    return str(local_candidate), False


def clamp_blast_threads(requested: int | None, *, parallel_jobs: int = 1) -> int:
    """
    BLAST の num_threads を決める。

    このアプリでは「合計スレッド予算」を入力する前提にする。
    （複数 DB を同時に叩くときも、CPU を使い切りつつ暴走を避ける）

    - requested が指定されていれば、それを 1..min(cpu_count, cap) にクランプした上で
      「同時に走るジョブ数」で割った値を採用（最低 1）
      （例: requested=24, DB を 6 つ並列 → 1ジョブあたり 24/6=4 threads）
    - 未指定なら max_threads を「同時に走るジョブ数」で割る
    """
    cores = cpu_count() or 2
    max_threads = max(1, min(_BLAST_THREADS_CAP, cores))
    jobs = max(1, int(parallel_jobs) if parallel_jobs else 1)
    if requested is not None:
        try:
            val = int(requested)
        except (TypeError, ValueError):
            val = 1
        budget = max(1, min(val, max_threads))
        return max(1, budget // jobs)

    return max(1, max_threads // jobs)


def _blast_bin(name: str) -> str:
    """
    BLAST バイナリの解決を行う。

    - BLAST_BIN_DIR が設定されていれば優先する。
    - それ以外は PATH を使う。
    """
    bin_dir = os.getenv("BLAST_BIN_DIR")
    if bin_dir:
        candidate = Path(bin_dir) / name
        if candidate.exists() and candidate.is_file():
            # 一部環境では BLAST_BIN_DIR 側の blastn が壊れていることがある
            # （例: 古いラッパが -mt_mode 2 を付与してしまい、BLAST+ 2.12 でエラーになる）。
            # その場合は PATH 側の blastn にフォールバックして、UI 側の BLAST を止めない。
            if name == "blastn" and not _validate_blastn_candidate(str(candidate)):
                logging.getLogger(__name__).warning(
                    "Ignoring BLAST_BIN_DIR blastn=%s (failed sanity check); falling back to PATH",
                    candidate,
                )
            else:
                return str(candidate)
    return name


@lru_cache(maxsize=16)
def _validate_blastn_candidate(path: str) -> bool:
    """
    BLAST_BIN_DIR 配下の blastn が安全に実行できるかを簡易チェックする。

    - `blastn -version` が 0 で返ること
    - `mt_mode` エラー（特に `2`）などで落ちるラッパ/ビルドを弾く
    """
    try:
        proc = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return False
    if proc.returncode == 0:
        return True
    stderr = (proc.stderr or "")
    if "mt_mode" in stderr and "Illegal value" in stderr:
        return False
    # その他の理由で落ちる場合も安全のため false
    return False


def _db_exists(db_prefix: Path, *, db_type: str = "nucl") -> bool:
    kind = (db_type or "nucl").strip().lower()
    if kind == "prot":
        suffixes = (".phr", ".pin", ".psq", ".pdb", ".pot", ".ptf", ".pto")
    else:
        suffixes = (".nhr", ".nin", ".nsq", ".ndb", ".not", ".ntf", ".nto")
    candidate_files = [db_prefix.with_suffix(suffix) for suffix in suffixes]
    return any(p.exists() for p in candidate_files)


@lru_cache(maxsize=64)
def _get_db_total_bases(db_prefix: str) -> int | None:
    """
    BLAST DB の total bases を取得する（-dbsize 用）。
    """
    cmd = [_blast_bin("blastdbcmd"), "-info", "-exact_length", "-db", db_prefix]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    m = re.search(r"([0-9][0-9,]*) total bases", proc.stdout)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def _has_megablast_index(db_prefix: Path) -> bool:
    return db_prefix.with_suffix(".00.idx").exists()


def _parse_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_flag(name: str) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_flag_default(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _blast_timeout_sec() -> int | None:
    """
    BLAST 系 CLI のタイムアウト秒を返す。
    0 以下ならタイムアウト無効（デフォルト: 無制限）。
    設計方針: 時間がかかっても全クエリを計算完了させる。
    """
    timeout = _parse_env_int("BLAST_SUBPROCESS_TIMEOUT_SEC", 0)
    if timeout <= 0:
        return None
    return timeout


def _blast_cuda_timeout_sec() -> int | None:
    """
    CUDA BLAST 実行専用タイムアウト。

    - BLAST_CUDA_TIMEOUT_SEC を優先
    - 未設定/0 以下なら BLAST_SUBPROCESS_TIMEOUT_SEC を使う
    - どちらも 0 以下なら無制限
    """
    timeout = _parse_env_int("BLAST_CUDA_TIMEOUT_SEC", -1)
    if timeout > 0:
        return timeout
    return _blast_timeout_sec()


def _blast_cuda_gate_enabled() -> bool:
    return _env_flag_default("BLAST_CUDA_GATE_ENABLE", True)


def _blast_cuda_equiv_tolerance_pct() -> float:
    return max(0.0, min(100.0, _parse_env_float("BLAST_CUDA_EQUIV_TOLERANCE_PCT", 0.5)))


def _blast_cuda_top1_min() -> float:
    default_min = 1.0 - (_blast_cuda_equiv_tolerance_pct() / 100.0)
    return max(0.0, min(1.0, _parse_env_float("BLAST_CUDA_TOP1_MIN", default_min)))


def _blast_cuda_top5_overlap_min() -> float:
    default_min = 1.0 - (_blast_cuda_equiv_tolerance_pct() / 100.0)
    return max(0.0, min(1.0, _parse_env_float("BLAST_CUDA_TOP5_OVERLAP_MIN", default_min)))


def _blast_cuda_bitscore_median_max_delta() -> float:
    return max(0.0, _parse_env_float("BLAST_CUDA_BITSCORE_MEDIAN_MAX_DELTA", 5.0))


def _blast_cuda_bitscore_median_max_delta_ratio() -> float:
    return max(
        0.0,
        _parse_env_float(
            "BLAST_CUDA_BITSCORE_MEDIAN_MAX_DELTA_RATIO",
            _blast_cuda_equiv_tolerance_pct() / 100.0,
        ),
    )


def _blast_cuda_shadow_rate() -> float:
    return max(0.0, min(1.0, _parse_env_float("BLAST_CUDA_SHADOW_RATE", 0.0)))


def _blast_cuda_force() -> bool:
    return _env_flag("BLAST_CUDA_FORCE")


def _blast_cuda_autotune_enabled() -> bool:
    return _env_flag_default("BLAST_CUDA_AUTOTUNE", True)


def _blast_cuda_auto_skip_small_enabled() -> bool:
    return _env_flag_default("BLAST_CUDA_AUTO_SKIP_SMALL", True)


def _blast_cuda_skip_any_max_total_bp() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_ANY_MAX_TOTAL_BP", 1500))


def _blast_cuda_skip_short_min_queries() -> int:
    return max(1, _parse_env_int("BLAST_CUDA_SKIP_SHORT_MIN_QUERIES", 8))


def _blast_cuda_skip_short_query_bp() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_SHORT_QUERY_BP", 80))


def _blast_cuda_skip_short_max_total_bp() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_SHORT_MAX_TOTAL_BP", 20000))


def _blast_cuda_skip_blastn_short_max_total_bp() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_BLASTN_SHORT_MAX_TOTAL_BP", 12000))


def _blast_cuda_skip_blastn_short_max_query_bp() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_BLASTN_SHORT_MAX_QUERY_BP", 200))


def _blast_cuda_skip_blastn_max_total_bp() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_BLASTN_MAX_TOTAL_BP", 20000))


def _blast_cuda_skip_blastn_max_query_bp() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_BLASTN_MAX_QUERY_BP", 2000))


def _blast_cuda_skip_blastn_max_queries() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_BLASTN_MAX_QUERIES", 64))


def _blast_cuda_skip_megablast_max_total_bp() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_MEGABLAST_MAX_TOTAL_BP", 20000))


def _blast_cuda_skip_megablast_max_query_bp() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_MEGABLAST_MAX_QUERY_BP", 2000))


def _blast_cuda_skip_megablast_max_queries() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_MEGABLAST_MAX_QUERIES", 64))


def _blast_cuda_skip_long_query_bp() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_LONG_QUERY_BP", 3000))


def _blast_cuda_skip_long_max_queries() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_SKIP_LONG_MAX_QUERIES", 8))


def _blast_cuda_gate_sample_queries() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_GATE_SAMPLE_QUERIES", 16))


def _blast_cuda_quarantine_ttl_sec() -> int:
    return max(0, _parse_env_int("BLAST_CUDA_QUARANTINE_TTL_SEC", 21600))


def _blast_fetch_sequence_timeout_sec() -> int | None:
    """
    blastdbcmd（配列切り出し）用のタイムアウト秒を返す。

    - BLAST_FETCH_SEQUENCE_TIMEOUT_SEC を優先
    - デフォルト: 無制限（0）
    - 0 以下ならタイムアウト無効
    """
    timeout = _parse_env_int("BLAST_FETCH_SEQUENCE_TIMEOUT_SEC", 0)
    if timeout <= 0:
        return None
    return timeout


def _blast_batch_max_total_hits() -> int:
    return max(1, _parse_env_int("BLAST_BATCH_MAX_TOTAL_HITS", 120_000))


def _blast_single_max_total_hits() -> int:
    return max(1, _parse_env_int("BLAST_SINGLE_MAX_TOTAL_HITS", 200_000))


def _blast_alignment_max_total_hits() -> int:
    return max(1, _parse_env_int("BLAST_ALIGNMENT_MAX_TOTAL_HITS", 50_000))


def _cuda_strict_mode() -> bool:
    return _env_flag("BLAST_CUDA_STRICT")


def _clip_text(text: str, max_chars: int = 4000) -> str:
    s = (text or "").strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "\n...[truncated]"


def _looks_like_missing_shared_library(stderr: str) -> bool:
    s = (stderr or "").lower()
    if not s:
        return False
    if "error while loading shared libraries" in s:
        return True
    if "cannot open shared object file" in s and "no such file or directory" in s:
        return True
    return "libcudart.so" in s and "not found" in s


def _looks_like_fatal_cuda_runtime_error(stderr: str) -> bool:
    s = (stderr or "").lower()
    if not s:
        return False
    fatal_markers = (
        "cuda error",
        "illegal memory access",
        "no kernel image is available",
        "unspecified launch failure",
        "driver version is insufficient",
        "unsupported gpu architecture",
        "cublas_status",
        "cudart",
    )
    return any(marker in s for marker in fatal_markers)


def _probe_binary_runtime(
    binary_path: str,
    *,
    probe_args: list[str] | None = None,
    timeout_sec: int = 5,
) -> tuple[bool, str | None]:
    """
    実行ファイルが「起動できるか」だけを軽く検査する。
    """
    cmd = [binary_path, *(probe_args or [])]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return True, None
    except OSError as exc:
        return False, str(exc)

    combined = "\n".join([(proc.stderr or ""), (proc.stdout or "")]).strip()
    if proc.returncode in (126, 127) and _looks_like_missing_shared_library(combined):
        return False, _clip_text(combined, max_chars=500)
    return True, None


def _disable_cuda_backend(reason: str) -> None:
    global CUDA_AVAILABLE, CUDA_UNAVAILABLE_REASON
    msg = reason.strip() or "unknown reason"
    if CUDA_AVAILABLE:
        logging.getLogger(__name__).warning("Disabling CUDA BLAST backend: %s", msg)
    CUDA_AVAILABLE = False
    CUDA_UNAVAILABLE_REASON = msg


def _raise_process_error(cmd: list[str], returncode: int, stderr: str) -> None:
    tool = Path(cmd[0]).name if cmd else "process"
    if returncode < 0:
        sig = -returncode
        if sig == 9:
            reason = f"signal={sig} (SIGKILL: OOM または OS による強制終了の可能性)"
        else:
            reason = f"signal={sig}"
    else:
        reason = f"returncode={returncode}"
    raise BlastExecutionError(
        f"{tool} の実行に失敗しました（{reason}）。\nstderr:\n{_clip_text(stderr)}"
    )


def _run_command_with_timeout(cmd: list[str], timeout_sec: int | None) -> subprocess.CompletedProcess[str]:
    """
    subprocess.run の薄いラッパ。

    timeout 時にプロセスグループごと kill して、残留プロセスを減らす。
    """
    if timeout_sec is None:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        proc.communicate()
        raise exc

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
    )


def _sanitize_env_task_token(task: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", (task or "").strip().lower()).strip("_")
    return token or "blastn"


def _cuda_task_defaults(task: str) -> dict[str, object]:
    t = (task or "blastn").strip().lower()
    if t == "blastn-short":
        return {
            "task": "blastn-short",
            "word_size": 7,
            "reward": 1,
            "penalty": -3,
            "gapopen": 5,
            "gapextend": 2,
            "xdrop_ungap": 12,
            "stats_mode": "ungapped",
            "dust": "no",
            "soft_masking": "false",
            "seed_index_mode": "dense",
            "max_segments": 256,
            "max_kmer_occ": 120,
            "max_seeds": 300000,
        }
    if t == "megablast":
        return {
            "task": "megablast",
            "word_size": 28,
            "reward": 1,
            "penalty": -2,
            "gapopen": 5,
            "gapextend": 2,
            "xdrop_ungap": 20,
            "stats_mode": "gapped",
            "dust": "yes",
            "soft_masking": "true",
            "seed_index_mode": "sparse",
            "max_segments": 512,
            "max_kmer_occ": 200,
            "max_seeds": 400000,
        }
    if t == "dc-megablast":
        return {
            "task": "dc-megablast",
            "word_size": 11,
            "reward": 2,
            "penalty": -3,
            "gapopen": 5,
            "gapextend": 2,
            "xdrop_ungap": 20,
            "stats_mode": "gapped",
            "dust": "yes",
            "soft_masking": "true",
            "seed_index_mode": "dense",
            "max_segments": 512,
            "max_kmer_occ": 120,
            "max_seeds": 400000,
        }
    return {
        "task": "blastn",
        "word_size": 11,
        "reward": 2,
        "penalty": -3,
        "gapopen": 5,
        "gapextend": 2,
        "xdrop_ungap": 20,
        "stats_mode": "gapped",
        "dust": "yes",
        "soft_masking": "true",
        "seed_index_mode": "dense",
        "max_segments": 512,
        "max_kmer_occ": 120,
        "max_seeds": 400000,
    }


def _cuda_task_params(task: str, max_hsps: int | None) -> dict[str, object]:
    params = dict(_cuda_task_defaults(task))
    token = _sanitize_env_task_token(task).upper()
    prefix = f"BLAST_CUDA_PARAMS_{token}_"

    def env_int(name: str, current: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
        raw = os.getenv(prefix + name)
        if raw is None:
            return current
        try:
            v = int(raw.strip())
        except ValueError:
            return current
        if minimum is not None:
            v = max(minimum, v)
        if maximum is not None:
            v = min(maximum, v)
        return v

    def env_str(name: str, current: str) -> str:
        raw = os.getenv(prefix + name)
        if raw is None:
            return current
        s = raw.strip()
        return s if s else current

    params["word_size"] = env_int("WORD_SIZE", int(params["word_size"]), minimum=4, maximum=28)
    params["reward"] = env_int("REWARD", int(params["reward"]), minimum=0, maximum=10)
    params["penalty"] = env_int("PENALTY", int(params["penalty"]), minimum=-20, maximum=0)
    params["gapopen"] = env_int("GAPOPEN", int(params["gapopen"]), minimum=0, maximum=50)
    params["gapextend"] = env_int("GAPEXTEND", int(params["gapextend"]), minimum=0, maximum=50)
    params["xdrop_ungap"] = env_int("XDROP_UNGAP", int(params["xdrop_ungap"]), minimum=1, maximum=200)
    params["stats_mode"] = env_str("STATS_MODE", str(params["stats_mode"])).lower()
    if params["stats_mode"] not in {"gapped", "ungapped"}:
        params["stats_mode"] = str(_cuda_task_defaults(task)["stats_mode"])

    params["dust"] = env_str("DUST", str(params["dust"])).lower()
    params["soft_masking"] = env_str("SOFT_MASKING", str(params["soft_masking"])).lower()
    params["seed_index_mode"] = env_str("SEED_INDEX_MODE", str(params["seed_index_mode"])).lower()
    if params["seed_index_mode"] not in {"dense", "sparse"}:
        params["seed_index_mode"] = str(_cuda_task_defaults(task)["seed_index_mode"])

    params["max_segments"] = env_int("MAX_SEGMENTS", int(params["max_segments"]), minimum=1, maximum=10000)
    params["max_kmer_occ"] = env_int("MAX_KMER_OCC", int(params["max_kmer_occ"]), minimum=1, maximum=10000)
    params["max_seeds"] = env_int("MAX_SEEDS", int(params["max_seeds"]), minimum=1000, maximum=5_000_000)
    if max_hsps is not None:
        params["max_hsps"] = max(1, int(max_hsps))
    else:
        env_max_hsps = os.getenv(prefix + "MAX_HSPS")
        params["max_hsps"] = max(1, int(env_max_hsps)) if env_max_hsps and env_max_hsps.isdigit() else None

    dbg_dump = (os.getenv(prefix + "EQUIV_DEBUG_DUMP") or "").strip()
    params["equiv_debug_dump"] = dbg_dump or None
    return params


def _cuda_workload_lengths(sequences: list[str]) -> list[int]:
    return [len((s or "").strip()) for s in sequences if s]


def _cuda_workload_signature(db: str, task: str, sequences: list[str]) -> str | None:
    lengths = _cuda_workload_lengths(sequences)
    if not lengths:
        return None
    num_queries = len(lengths)
    total_bp = sum(lengths)
    max_bp = max(lengths)
    min_bp = min(lengths)
    avg_bp = int(round(total_bp / max(1, num_queries)))
    payload = (
        f"{normalize_db_prefix(db)}|{(task or '').strip().lower()}|"
        f"n={num_queries}|total={total_bp}|max={max_bp}|min={min_bp}|avg={avg_bp}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cuda_quarantine_reason_for_workload(db: str, task: str, sequences: list[str]) -> str | None:
    ttl = _blast_cuda_quarantine_ttl_sec()
    if ttl <= 0:
        return None
    key = _cuda_workload_signature(db, task, sequences)
    if not key:
        return None
    now = time.time()
    with _CUDA_QUARANTINE_LOCK:
        row = _CUDA_WORKLOAD_QUARANTINE.get(key)
        if row is None:
            return None
        expires_at, reason = row
        if expires_at <= now:
            _CUDA_WORKLOAD_QUARANTINE.pop(key, None)
            return None
    return reason


def _remember_cuda_quarantine(db: str, task: str, sequences: list[str], reason: str) -> None:
    ttl = _blast_cuda_quarantine_ttl_sec()
    if ttl <= 0:
        return
    key = _cuda_workload_signature(db, task, sequences)
    if not key:
        return
    msg = _clip_text(reason or "cuda workload quarantined", max_chars=500)
    with _CUDA_QUARANTINE_LOCK:
        _CUDA_WORKLOAD_QUARANTINE[key] = (time.time() + ttl, msg)


def _clear_cuda_quarantine_cache_for_tests() -> None:
    with _CUDA_QUARANTINE_LOCK:
        _CUDA_WORKLOAD_QUARANTINE.clear()


def _select_cuda_gate_sample_indices(total_queries: int) -> list[int]:
    if total_queries <= 0:
        return []
    sample_cap = _blast_cuda_gate_sample_queries()
    if sample_cap <= 0 or total_queries <= sample_cap:
        return list(range(total_queries))
    if sample_cap == 1:
        return [0]
    raw = {
        min(total_queries - 1, int(round(i * (total_queries - 1) / (sample_cap - 1))))
        for i in range(sample_cap)
    }
    out = sorted(raw)
    if len(out) >= sample_cap:
        return out[:sample_cap]
    seen = set(out)
    for idx in range(total_queries):
        if idx in seen:
            continue
        out.append(idx)
        seen.add(idx)
        if len(out) >= sample_cap:
            break
    out.sort()
    return out[:sample_cap]


def _cuda_skip_reason_for_workload(task: str, sequences: list[str]) -> str | None:
    if not sequences:
        return None
    if _cuda_strict_mode() or _blast_cuda_force() or not _blast_cuda_auto_skip_small_enabled():
        return None

    lengths = _cuda_workload_lengths(sequences)
    if not lengths:
        return None

    task_norm = (task or "").strip().lower()
    num_queries = len(lengths)
    total_bp = sum(lengths)
    max_bp = max(lengths)

    tiny_total_bp_max = _blast_cuda_skip_any_max_total_bp()
    if tiny_total_bp_max > 0 and total_bp <= tiny_total_bp_max:
        return f"tiny workload (queries={num_queries}, total_bp={total_bp})"

    long_query_bp = _blast_cuda_skip_long_query_bp()
    long_max_queries = _blast_cuda_skip_long_max_queries()
    if (
        task_norm in {"blastn", "megablast", "dc-megablast"}
        and long_query_bp > 0
        and max_bp >= long_query_bp
        and (long_max_queries <= 0 or num_queries <= long_max_queries)
    ):
        return (
            "long-query workload currently CPU-preferred "
            f"(queries={num_queries}, max_bp={max_bp})"
        )

    short_task_total_bp_max = _blast_cuda_skip_blastn_short_max_total_bp()
    short_task_max_bp = _blast_cuda_skip_blastn_short_max_query_bp()
    if (
        task_norm == "blastn-short"
        and short_task_total_bp_max > 0
        and total_bp <= short_task_total_bp_max
        and (short_task_max_bp <= 0 or max_bp <= short_task_max_bp)
    ):
        return (
            "blastn-short workload below CUDA threshold "
            f"(queries={num_queries}, total_bp={total_bp}, max_bp={max_bp})"
        )

    blastn_total_bp_max = _blast_cuda_skip_blastn_max_total_bp()
    blastn_max_bp = _blast_cuda_skip_blastn_max_query_bp()
    blastn_max_queries = _blast_cuda_skip_blastn_max_queries()
    if (
        task_norm == "blastn"
        and blastn_total_bp_max > 0
        and total_bp <= blastn_total_bp_max
        and (blastn_max_bp <= 0 or max_bp <= blastn_max_bp)
        and (blastn_max_queries <= 0 or num_queries <= blastn_max_queries)
    ):
        return (
            "blastn workload below CUDA threshold "
            f"(queries={num_queries}, total_bp={total_bp}, max_bp={max_bp})"
        )

    megablast_total_bp_max = _blast_cuda_skip_megablast_max_total_bp()
    megablast_max_bp = _blast_cuda_skip_megablast_max_query_bp()
    megablast_max_queries = _blast_cuda_skip_megablast_max_queries()
    if (
        task_norm in {"megablast", "dc-megablast"}
        and megablast_total_bp_max > 0
        and total_bp <= megablast_total_bp_max
        and (megablast_max_bp <= 0 or max_bp <= megablast_max_bp)
        and (megablast_max_queries <= 0 or num_queries <= megablast_max_queries)
    ):
        return (
            "megablast-family workload below CUDA threshold "
            f"(queries={num_queries}, total_bp={total_bp}, max_bp={max_bp})"
        )

    short_bp_max = _blast_cuda_skip_short_query_bp()
    short_total_bp_max = _blast_cuda_skip_short_max_total_bp()
    short_min_queries = _blast_cuda_skip_short_min_queries()
    if (
        short_bp_max > 0
        and max_bp <= short_bp_max
        and num_queries >= short_min_queries
        and (short_total_bp_max <= 0 or total_bp <= short_total_bp_max)
    ):
        return (
            "short-query workload below CUDA threshold "
            f"(queries={num_queries}, total_bp={total_bp}, max_bp={max_bp})"
        )
    return None


def _autotune_cuda_params_for_workload(
    *,
    task: str,
    params: dict[str, object],
    sequences: list[str] | None,
) -> dict[str, object]:
    if not _blast_cuda_autotune_enabled():
        return params
    if not sequences:
        return params

    t = (task or "").strip().lower()
    if t != "blastn-short":
        return params

    lengths = [len((s or "").strip()) for s in sequences if s]
    if not lengths:
        return params

    total_bp = sum(lengths)
    token = _sanitize_env_task_token(t).upper()
    p = dict(params)

    if total_bp <= 2000:
        target_segments = 64
        target_occ = 60
        target_seeds = 100000
    elif total_bp <= 8000:
        target_segments = 128
        target_occ = 80
        target_seeds = 150000
    else:
        target_segments = 256
        target_occ = 120
        target_seeds = 300000

    if os.getenv(f"BLAST_CUDA_PARAMS_{token}_MAX_SEGMENTS") is None:
        p["max_segments"] = min(int(p.get("max_segments", target_segments)), target_segments)
    if os.getenv(f"BLAST_CUDA_PARAMS_{token}_MAX_KMER_OCC") is None:
        p["max_kmer_occ"] = min(int(p.get("max_kmer_occ", target_occ)), target_occ)
    if os.getenv(f"BLAST_CUDA_PARAMS_{token}_MAX_SEEDS") is None:
        p["max_seeds"] = min(int(p.get("max_seeds", target_seeds)), target_seeds)
    return p


def _hit_key_for_equiv(hit: BlastHit) -> tuple[str, int, int]:
    sid_raw = (hit.sseqid or "").split()[0]
    sid = _unwrap_seqid_wrapper(sid_raw) or sid_raw
    s0 = min(int(hit.sstart), int(hit.send))
    s1 = max(int(hit.sstart), int(hit.send))
    return sid, s0, s1


def _evaluate_cuda_equivalence(
    gpu_hits_by_query: list[list[BlastHit]],
    cpu_hits_by_query: list[list[BlastHit]],
) -> BlastEquivalenceGateResult:
    gate = BlastEquivalenceGateResult(
        enabled=True,
        passed=True,
        threshold_top1_min=_blast_cuda_top1_min(),
        threshold_top5_overlap_min=_blast_cuda_top5_overlap_min(),
        threshold_bitscore_median_max_delta=_blast_cuda_bitscore_median_max_delta(),
        threshold_bitscore_median_max_delta_ratio=_blast_cuda_bitscore_median_max_delta_ratio(),
    )

    compared = min(len(gpu_hits_by_query), len(cpu_hits_by_query))
    gate.compared_queries = compared
    if compared <= 0:
        gate.note = "no comparable queries"
        return gate

    top1_matches = 0
    top5_overlaps: list[float] = []
    bitscore_deltas: list[float] = []
    bitscore_delta_ratios: list[float] = []

    for i in range(compared):
        gpu_hits = gpu_hits_by_query[i] or []
        cpu_hits = cpu_hits_by_query[i] or []

        gpu_top1 = gpu_hits[0] if gpu_hits else None
        cpu_top1 = cpu_hits[0] if cpu_hits else None
        if gpu_top1 is None and cpu_top1 is None:
            top1_matches += 1
        elif gpu_top1 is not None and cpu_top1 is not None:
            if _hit_key_for_equiv(gpu_top1) == _hit_key_for_equiv(cpu_top1):
                top1_matches += 1

        gpu_top5 = gpu_hits[:5]
        cpu_top5 = cpu_hits[:5]
        gpu_map = {_hit_key_for_equiv(h): float(h.bitscore) for h in gpu_top5}
        cpu_map = {_hit_key_for_equiv(h): float(h.bitscore) for h in cpu_top5}
        gpu_keys = set(gpu_map.keys())
        cpu_keys = set(cpu_map.keys())
        if not gpu_keys and not cpu_keys:
            top5_overlaps.append(1.0)
        elif not cpu_keys:
            top5_overlaps.append(0.0)
        else:
            inter = gpu_keys & cpu_keys
            top5_overlaps.append(len(inter) / max(1, len(cpu_keys)))
            for k in inter:
                cpu_score = float(cpu_map[k])
                delta = abs(float(gpu_map[k]) - cpu_score)
                bitscore_deltas.append(delta)
                bitscore_delta_ratios.append(delta / max(1e-9, abs(cpu_score)))

    gate.top1_match_rate = top1_matches / max(1, compared)
    gate.top5_overlap_rate = sum(top5_overlaps) / max(1, len(top5_overlaps))
    gate.bitscore_median_delta = (
        float(statistics.median(bitscore_deltas))
        if bitscore_deltas
        else (
            0.0
            if all((not g and not c) for g, c in zip(gpu_hits_by_query[:compared], cpu_hits_by_query[:compared], strict=False))
            else 1_000_000_000.0
        )
    )
    gate.bitscore_median_delta_ratio = (
        float(statistics.median(bitscore_delta_ratios))
        if bitscore_delta_ratios
        else (
            0.0
            if all((not g and not c) for g, c in zip(gpu_hits_by_query[:compared], cpu_hits_by_query[:compared], strict=False))
            else 1_000_000_000.0
        )
    )

    reasons: list[str] = []
    if gate.top1_match_rate < (gate.threshold_top1_min or 0.0):
        reasons.append(
            f"top1_match_rate={gate.top1_match_rate:.3f} < {gate.threshold_top1_min:.3f}"
        )
    if gate.top5_overlap_rate < (gate.threshold_top5_overlap_min or 0.0):
        reasons.append(
            f"top5_overlap_rate={gate.top5_overlap_rate:.3f} < {gate.threshold_top5_overlap_min:.3f}"
        )
    if (gate.bitscore_median_delta or 0.0) > (gate.threshold_bitscore_median_max_delta or 0.0):
        reasons.append(
            f"bitscore_median_delta={gate.bitscore_median_delta:.3f} > {gate.threshold_bitscore_median_max_delta:.3f}"
        )
    if (gate.bitscore_median_delta_ratio or 0.0) > (gate.threshold_bitscore_median_max_delta_ratio or 0.0):
        reasons.append(
            "bitscore_median_delta_ratio="
            f"{(gate.bitscore_median_delta_ratio or 0.0) * 100.0:.3f}% > "
            f"{(gate.threshold_bitscore_median_max_delta_ratio or 0.0) * 100.0:.3f}%"
        )
    gate.passed = not reasons
    if reasons:
        gate.note = " / ".join(reasons)
    return gate


def _maybe_run_cuda_shadow_compare(
    gpu_hits_by_query: list[list[BlastHit]],
    cpu_hits_by_query: list[list[BlastHit]],
    *,
    gate_enabled: bool,
    logger: logging.Logger,
    label: str,
) -> BlastEquivalenceGateResult | None:
    if gate_enabled:
        return _evaluate_cuda_equivalence(gpu_hits_by_query, cpu_hits_by_query)
    if random.random() > _blast_cuda_shadow_rate():
        return None
    gate = _evaluate_cuda_equivalence(gpu_hits_by_query, cpu_hits_by_query)
    gate.enabled = False
    note = gate.note or ""
    gate.note = f"shadow_compare: {note}".strip()
    logger.info(
        "cuda shadow compare (%s): top1=%.3f top5=%.3f bitscore_delta=%s bitscore_delta_ratio=%s passed=%s",
        label,
        gate.top1_match_rate or 0.0,
        gate.top5_overlap_rate or 0.0,
        f"{gate.bitscore_median_delta:.3f}" if gate.bitscore_median_delta is not None else "n/a",
        f"{(gate.bitscore_median_delta_ratio or 0.0) * 100.0:.3f}%"
        if gate.bitscore_median_delta_ratio is not None
        else "n/a",
        gate.passed,
    )
    return gate


@lru_cache(maxsize=64)
def _get_db_sequence_count(db_prefix: str) -> int | None:
    cmd = [_blast_bin("blastdbcmd"), "-info", "-db", db_prefix]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    m = re.search(r"([0-9][0-9,]*) sequences;", proc.stdout)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def _db_fingerprint(db_prefix: Path) -> str:
    for suffix in (".nsq", ".ndb", ".nhr", ".nin"):
        p = db_prefix.with_suffix(suffix)
        if p.exists():
            st = p.stat()
            return f"{p.name}:{st.st_mtime_ns}:{st.st_size}"
    return db_prefix.name


def _gpu_prefilter_cache_dir() -> Path:
    root = blast_gpu_prefilter_cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _gpu_prefilter_cache_ttl() -> int | None:
    # 既定は 6h にして、容量を食い続けないようにする。
    ttl = _parse_env_int("BLAST_GPU_PREFILTER_CACHE_TTL_SEC", 6 * 60 * 60)
    if ttl <= 0:
        return None
    return ttl


def _gpu_prefilter_cache_max_bytes() -> int | None:
    # 既定 1GB。0 以下で無制限。
    max_mb = _parse_env_int("BLAST_GPU_PREFILTER_CACHE_MAX_MB", 1024)
    if max_mb <= 0:
        return None
    return max_mb * 1024 * 1024


def _cleanup_gpu_prefilter_cache() -> None:
    """
    GPU prefilter キャッシュを掃除する（best-effort）。

    - TTL 超過ファイルを削除
    - 総容量が上限を超えたら古いファイルから削除
    """
    cache_dir = _gpu_prefilter_cache_dir()
    ttl = _gpu_prefilter_cache_ttl()
    max_bytes = _gpu_prefilter_cache_max_bytes()
    now = time.time()

    candidates: list[tuple[Path, int, float]] = []
    for p in cache_dir.iterdir():
        if not p.is_file():
            continue
        if p.suffix not in {".seqidlist", ".bsl", ".count", ".tmp"}:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if ttl is not None and (now - st.st_mtime) > ttl:
            try:
                p.unlink()
            except OSError:
                pass
            continue
        candidates.append((p, int(st.st_size), float(st.st_mtime)))

    if max_bytes is None:
        return

    total = sum(size for _p, size, _mt in candidates)
    if total <= max_bytes:
        return

    # 上限を超えたら 90% まで古い順に削る。
    target = int(max_bytes * 0.9)
    for p, size, _mt in sorted(candidates, key=lambda x: x[2]):
        if total <= target:
            break
        try:
            p.unlink()
            total -= size
        except OSError:
            continue


def _prefilter_cache_paths(cache_key: str) -> tuple[Path, Path, Path]:
    cache_dir = _gpu_prefilter_cache_dir()
    return (
        cache_dir / f"{cache_key}.seqidlist",
        cache_dir / f"{cache_key}.bsl",
        cache_dir / f"{cache_key}.count",
    )


def _prefilter_cache_valid(path: Path) -> bool:
    if not path.exists():
        return False
    ttl = _gpu_prefilter_cache_ttl()
    if ttl is None:
        return True
    age = time.time() - path.stat().st_mtime
    return age <= ttl


def _prefilter_cache_key(db_prefix: Path, word_size: int, sequences: list[str]) -> str:
    h = hashlib.sha256()
    h.update(str(db_prefix).encode("utf-8"))
    h.update(b"|")
    h.update(_db_fingerprint(db_prefix).encode("utf-8"))
    h.update(b"|")
    h.update(f"k={word_size}".encode("utf-8"))
    for seq in sequences:
        h.update(b"|")
        h.update(seq.encode("utf-8"))
    return h.hexdigest()


def _read_seqidlist_count(seqidlist_path: Path, count_path: Path) -> int:
    if count_path.exists():
        try:
            return int(count_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
    count = 0
    with seqidlist_path.open("r", encoding="utf-8", errors="ignore") as fh:
        for _ in fh:
            count += 1
    try:
        count_path.write_text(str(count), encoding="utf-8")
    except OSError:
        pass
    return count


def _default_word_size_for_task(task: str) -> int:
    t = (task or "").lower()
    if t == "blastn-short":
        return 7
    if t == "megablast":
        return 28
    if t == "dc-megablast":
        return 11
    return 11


def _effective_blastn_task_for_sequences(task: str, sequences: list[str]) -> str:
    """
    Short-query safety for blastn tasks.

    megablast can become unstable for primer-like short queries (e.g. 18-30bp),
    so downgrade to blastn-short unless explicitly disabled.
    """
    requested = (task or "").strip().lower() or "blastn"
    if requested != "megablast":
        return requested

    threshold = _parse_env_int("BLAST_MEGABLAST_SHORT_QUERY_BP", 60)
    if threshold <= 0:
        return requested

    min_bp = min((len((s or "").strip()) for s in sequences if s), default=0)
    if min_bp and min_bp < threshold:
        logging.getLogger(__name__).info(
            "blastn task override: requested=megablast -> blastn-short (min_query_bp=%s, threshold=%s)",
            min_bp,
            threshold,
        )
        return "blastn-short"
    return requested


def _gpu_prefilter_word_size(task: str) -> int:
    ws = _default_word_size_for_task(task)
    max_k = _parse_env_int("BLAST_GPU_PREFILTER_MAX_K", 12)
    if max_k > 0:
        ws = min(ws, max_k)
    return max(4, min(ws, 12))


def _gpu_prefilter_should_run(total_bp: int, num_queries: int) -> bool:
    min_bp = _parse_env_int("BLAST_GPU_PREFILTER_MIN_BP", 0)
    min_queries = _parse_env_int("BLAST_GPU_PREFILTER_MIN_QUERIES", 1)
    return total_bp >= min_bp and num_queries >= min_queries


def _resolve_gpu_prefilter_assets(db_prefix: Path) -> tuple[str, Path, int, int, bool]:
    prefilter_bin = os.getenv("BLAST_GPU_PREFILTER_BIN") or ""
    if prefilter_bin:
        candidate = Path(prefilter_bin)
        if candidate.is_absolute() and not candidate.exists():
            raise BlastExecutionError(f"GPU prefilter バイナリが見つかりません: {prefilter_bin}")
        bin_path = prefilter_bin
    else:
        bin_path = shutil.which("blastn_gpu_prefilter") or ""
    if not bin_path:
        raise BlastExecutionError("GPU prefilter バイナリが見つかりません。")

    prefilter_fasta = os.getenv("BLAST_GPU_PREFILTER_FASTA") or ""
    fasta_path: Path | None = None
    if prefilter_fasta:
        fasta_candidate = Path(prefilter_fasta)
        if fasta_candidate.is_absolute() and not fasta_candidate.exists():
            raise BlastExecutionError(f"GPU prefilter FASTA が見つかりません: {prefilter_fasta}")
        fasta_path = fasta_candidate
    else:
        for suffix in (".fa", ".fasta", ".fna"):
            candidate = db_prefix.with_suffix(suffix)
            if candidate.exists():
                fasta_path = candidate
                break
    if fasta_path is None:
        raise BlastExecutionError(f"GPU prefilter FASTA が見つかりません: {db_prefix}")

    batch_mb = _parse_env_int("BLAST_GPU_PREFILTER_BATCH_MB", 256)
    device = _parse_env_int("BLAST_GPU_PREFILTER_DEVICE", 0)
    force_cpu = _env_flag("BLAST_GPU_PREFILTER_FORCE_CPU")
    return bin_path, fasta_path, batch_mb, device, force_cpu


def _maybe_convert_seqidlist_to_bsl(
    seqidlist_path: Path,
    db_prefix: Path,
    seqid_count: int,
) -> Path | None:
    threshold = _parse_env_int("BLAST_GPU_PREFILTER_BSL_THRESHOLD", 1000)
    if seqid_count < threshold:
        return None
    bsl_path = seqidlist_path.with_suffix(".bsl")
    if bsl_path.exists():
        return bsl_path
    cmd = [
        _blast_bin("blastdb_aliastool"),
        "-seqid_file_in",
        str(seqidlist_path),
        "-seqid_db",
        str(db_prefix),
        "-seqid_dbtype",
        "nucl",
        "-seqid_file_out",
        str(bsl_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60)
    if proc.returncode != 0:
        return None
    return bsl_path


def _maybe_run_gpu_prefilter(
    query_path: Path,
    db_prefix: Path,
    sequences: list[str],
    task: str,
) -> tuple[Path, int] | None:
    _cleanup_gpu_prefilter_cache()
    if not _gpu_prefilter_should_run(sum(len(s) for s in sequences), len(sequences)):
        return None

    word_size = _gpu_prefilter_word_size(task)
    cache_key = _prefilter_cache_key(db_prefix, word_size, sequences)
    seqid_path, bsl_path, count_path = _prefilter_cache_paths(cache_key)

    if _prefilter_cache_valid(seqid_path):
        seqid_count = _read_seqidlist_count(seqid_path, count_path)
        if seqid_count == 0:
            return None
        total_seqs = _get_db_sequence_count(str(db_prefix))
        ratio_limit = _parse_env_float("BLAST_GPU_PREFILTER_MAX_SEQ_RATIO", 0.95)
        if total_seqs and seqid_count >= int(total_seqs * ratio_limit):
            return None
        use_bsl = _maybe_convert_seqidlist_to_bsl(seqid_path, db_prefix, seqid_count)
        return (use_bsl or seqid_path, seqid_count)

    with _GPU_PREFILTER_LOCK:
        if _prefilter_cache_valid(seqid_path):
            seqid_count = _read_seqidlist_count(seqid_path, count_path)
            if seqid_count == 0:
                return None
            total_seqs = _get_db_sequence_count(str(db_prefix))
            ratio_limit = _parse_env_float("BLAST_GPU_PREFILTER_MAX_SEQ_RATIO", 0.95)
            if total_seqs and seqid_count >= int(total_seqs * ratio_limit):
                return None
            use_bsl = _maybe_convert_seqidlist_to_bsl(seqid_path, db_prefix, seqid_count)
            return (use_bsl or seqid_path, seqid_count)

        try:
            bin_path, fasta_path, batch_mb, device, force_cpu = _resolve_gpu_prefilter_assets(db_prefix)
        except BlastExecutionError as exc:
            logging.getLogger(__name__).warning("GPU prefilter unavailable; skipping (db=%s): %s", db_prefix, exc)
            return None
        cache_dir = _gpu_prefilter_cache_dir()
        tmp_handle = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".seqidlist.tmp",
            delete=False,
            dir=cache_dir,
        )
        tmp_handle.close()
        tmp_path = Path(tmp_handle.name)

        cmd = [
            bin_path,
            "-query",
            str(query_path),
            "-db_fasta",
            str(fasta_path),
            "-out",
            str(tmp_path),
            "-word_size",
            str(word_size),
            "-batch_mb",
            str(batch_mb),
            "-device",
            str(device),
        ]
        if force_cpu:
            cmd.append("--cpu")

        gpu_timeout = _blast_timeout_sec()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=gpu_timeout)
        except subprocess.TimeoutExpired:
            tmp_path.unlink(missing_ok=True)
            logging.getLogger(__name__).warning(
                "GPU prefilter timed out (%ss); skipping (db=%s)", gpu_timeout, db_prefix,
            )
            return None
        if proc.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            return None

        tmp_path.replace(seqid_path)
        if not seqid_path.exists():
            return None

        seqid_count = _read_seqidlist_count(seqid_path, count_path)
        if seqid_count == 0:
            return None
        total_seqs = _get_db_sequence_count(str(db_prefix))
        ratio_limit = _parse_env_float("BLAST_GPU_PREFILTER_MAX_SEQ_RATIO", 0.95)
        if total_seqs and seqid_count >= int(total_seqs * ratio_limit):
            return None

        use_bsl = _maybe_convert_seqidlist_to_bsl(seqid_path, db_prefix, seqid_count)
        return (use_bsl or seqid_path, seqid_count)


def resolve_local_db_plan(db: str, local_mode: str | None) -> tuple[str, str, str]:
    """
    local_mode に応じた DB パスと表示ラベルを解決する。

    戻り値: (db_path, label, mode)
    - label は元の DB パスの末尾名（表示用）
    - mode は "gpu" または "cpu"
    """
    db = normalize_db_prefix(db)
    label = Path(db).name
    mode = (local_mode or "cpu").lower()
    if mode != "gpu":
        # GPU 用の派生DB（例: *_ps）が存在する場合、CPU モードでもそれを利用できる。
        # この派生DBは megablast の hash index（.00.idx）を持つことが多く、CPU でも高速化になる。
        #
        # ただし、派生DBが「同一内容とは限らない」環境もあり得るので、
        # 既定では「派生DBに index があり、元DBに index が無い」ケースだけ自動で切り替える。
        suffix = (os.getenv("BLAST_GPU_DB_SUFFIX") or "").strip()
        if suffix and not db.endswith(suffix):
            candidate, from_cold_root = _resolve_suffix_db_candidate(db, suffix)
            cand_prefix = Path(candidate)
            if _db_exists(cand_prefix):
                prefer_all = _env_flag("BLAST_PREFER_GPU_DB_SUFFIX_FOR_CPU")
                prefer_cold = _env_flag("BLAST_PREFER_COLD_GPU_DB_SUFFIX_FOR_CPU")
                prefer_indexed = _has_megablast_index(cand_prefix) and not _has_megablast_index(Path(db))
                if from_cold_root and not (prefer_all or prefer_cold):
                    prefer_indexed = False
                if prefer_all or prefer_indexed or (from_cold_root and prefer_cold):
                    return candidate, label, "cpu"

        return db, label, "cpu"

    suffix = (os.getenv("BLAST_GPU_DB_SUFFIX") or "").strip()
    if suffix:
        if db.endswith(suffix):
            return db, label, "gpu"
        candidate, _ = _resolve_suffix_db_candidate(db, suffix)
        if _db_exists(Path(candidate)):
            return candidate, label, "gpu"

    return db, label, "cpu"


def _resolve_gpu_prefilter_args(db_prefix: Path) -> list[str]:
    """
    GPU prefilter 用の引数を組み立てる。
    """
    args = ["-gpu_prefilter"]

    prefilter_bin = os.getenv("BLAST_GPU_PREFILTER_BIN")
    if prefilter_bin:
        candidate = Path(prefilter_bin)
        if candidate.is_absolute() and not candidate.exists():
            raise BlastExecutionError(f"GPU prefilter バイナリが見つかりません: {prefilter_bin}")
        args.extend(["-gpu_prefilter_bin", prefilter_bin])
    else:
        if shutil.which("blastn_gpu_prefilter"):
            args.extend(["-gpu_prefilter_bin", "blastn_gpu_prefilter"])

    prefilter_fasta = os.getenv("BLAST_GPU_PREFILTER_FASTA")
    if prefilter_fasta:
        fasta_path = Path(prefilter_fasta)
        if fasta_path.is_absolute() and not fasta_path.exists():
            raise BlastExecutionError(f"GPU prefilter FASTA が見つかりません: {prefilter_fasta}")
        args.extend(["-gpu_prefilter_fasta", prefilter_fasta])
    else:
        for suffix in (".fa", ".fasta", ".fna"):
            candidate = db_prefix.with_suffix(suffix)
            if candidate.exists():
                args.extend(["-gpu_prefilter_fasta", str(candidate)])
                break
    batch_mb = _parse_env_int("BLAST_GPU_PREFILTER_BATCH_MB", 0)
    if batch_mb > 0:
        args.extend(["-gpu_prefilter_batch_mb", str(batch_mb)])
    device = _parse_env_int("BLAST_GPU_PREFILTER_DEVICE", -1)
    if device >= 0:
        args.extend(["-gpu_prefilter_device", str(device)])
    max_k = _parse_env_int("BLAST_GPU_PREFILTER_MAX_K", 0)
    if max_k > 0:
        args.extend(["-gpu_prefilter_max_k", str(max_k)])
    if _env_flag("BLAST_GPU_PREFILTER_FORCE_CPU"):
        args.append("-gpu_prefilter_cpu")

    return args


def _normalize_chr_alias(token: str) -> str:
    """
    ユーザー入力の染色体表記を正規化する。

    例:
    - Chr1 / chr01 / 1 -> chr1
    - chrUn0001 -> chrUn0001（大小のみ整形）
    """
    t = (token or "").strip()
    if not t:
        return t

    m = re.match(r"^(?:chr)?0*([0-9]+)$", t, flags=re.IGNORECASE)
    if m:
        return f"chr{int(m.group(1))}"

    # chrUn0001 などはそのまま（prefix の大小だけ揃える）
    if t.lower().startswith("chrun"):
        return "chrUn" + t[5:]
    if t.lower().startswith("chr"):
        return "chr" + t[3:]
    return t


def _infer_chrom_from_seqid_and_title(seqid: str, title: str) -> str | None:
    # 例: "chromosome 1"
    m_chr = re.search(r"chromosome\s+([0-9A-Za-z]+)", title, flags=re.IGNORECASE)
    if m_chr:
        return _normalize_chr_alias(f"chr{m_chr.group(1)}")

    # 例: "chrUn0001"
    m_un = re.search(r"(chrUn[0-9A-Za-z_]+)", title)
    if m_un:
        return _normalize_chr_alias(m_un.group(1))

    # accession_e/JI*/accession_d/accession_f 系: "...ch1VI" / "...ch2I" など → chr1 / chr2
    m_ch = re.search(r"ch0*([0-9]+)", seqid, flags=re.IGNORECASE)
    if m_ch:
        return _normalize_chr_alias(f"chr{int(m_ch.group(1))}")

    # v1a: 1LG6 / 4LG4 など → chr1 / chr4
    m_lg = re.match(r"([0-9]+)LG[0-9A-Za-z]*", seqid)
    if m_lg:
        return _normalize_chr_alias(f"chr{m_lg.group(1)}")

    # 既に chr* ならそれを採用
    if seqid.lower().startswith("chr"):
        return _normalize_chr_alias(seqid)
    return None


def _build_alias_map_from_chrom_mapping(mapping: dict[str, str]) -> dict[str, str]:
    alias: dict[str, str] = {}
    for chrom, entry in mapping.items():
        if not isinstance(chrom, str) or not isinstance(entry, str):
            continue
        chrom_norm = _normalize_chr_alias(chrom)
        if not chrom_norm.lower().startswith("chr"):
            continue
        entry_norm = entry.strip()
        if not entry_norm:
            continue

        alias.setdefault(chrom_norm, entry_norm)
        alias.setdefault(chrom_norm.lower(), entry_norm)
        m_num = re.match(r"^chr0*([0-9]+)$", chrom_norm, flags=re.IGNORECASE)
        if m_num:
            alias.setdefault(str(int(m_num.group(1))), entry_norm)

        # entry 自身も受け入れる（大小無視）
        alias.setdefault(entry_norm, entry_norm)
        alias.setdefault(entry_norm.lower(), entry_norm)
    return alias


def _unwrap_seqid_wrapper(s: str) -> str:
    """
    典型的な BLAST/FASTA ラッパーを剥がす。
    例:
    - ref|NC_066579.1|
    - gb|ABC12345.1|
    - lcl|seq1
    - gnl|BL_ORD_ID|12345
    - pdb|1LG6|
    """
    t = (s or "").strip()
    if not t:
        return t
    token = t.split()[0]
    if "|" not in token:
        return token
    parts = [p for p in token.split("|") if p]
    p0 = parts[0].lower() if parts else ""
    if p0 in {"ref", "gb", "emb", "dbj", "sp", "tr", "lcl"} and len(parts) >= 2:
        return parts[1].strip()
    if p0 == "gnl" and len(parts) >= 3:
        return parts[-1].strip()
    # 汎用ラッパー: "xxx|seqid|" / "xxx|seqid"
    if len(parts) >= 2:
        prefix = parts[0]
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,15}", prefix):
            return parts[1].strip()
    return token


@lru_cache(maxsize=16)
def _load_entry_alias_map(db: str) -> dict[str, str]:
    """
    DB ごとの entry エイリアス（chr1 等）→実際の seqid のマップを構築する。

    - makeblastdb を -parse_seqids なしで作成した DB でも、blastdbcmd の title 出力から推定できる。
    - 実際の配列取得は blastdbcmd が失敗する場合があるため、本関数は「変換」だけを担う。
    """
    db_prefix = Path(db)
    candidate_files = [
        db_prefix.with_suffix(suffix)
        for suffix in (".nhr", ".nin", ".nsq", ".ndb", ".not", ".ntf", ".nto")
    ]
    if not any(p.exists() for p in candidate_files):
        return {}

    # 事前計算（キャッシュ）がある場合はそれだけで返す（blastdbcmd 全列挙を避ける）
    override = _load_chrom_alias_override(db)
    if override and isinstance(override.get("mapping"), dict):
        m = _build_alias_map_from_chrom_mapping(override.get("mapping") or {})  # type: ignore[arg-type]
        if m:
            return m

    proc = subprocess.run(
        [
            _blast_bin("blastdbcmd"),
            "-db",
            db,
            "-entry",
            "all",
            "-outfmt",
            "%o\t%a\t%t",
            "-long_seqids",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=_blast_timeout_sec(),
    )
    if proc.returncode != 0:
        return {}

    alias: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        # "%o\t%a\t%t"（title が空のDBがあるので accession を優先する）
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        accession = (parts[1] or "").strip()
        title = (parts[2] if len(parts) >= 3 else "").strip()
        # BLAST の sseqid は accession と一致することが多い。
        # 一部DB（reference_v1 など）は accession が "BL_ORD_ID:0" のような内部IDになるため、
        # その場合だけ title 先頭トークンを採用する。
        title_tok = (title.split()[0] if title else "").strip()
        is_internal_acc = accession.upper().startswith("BL_ORD_ID:")
        seqid = accession if accession and not is_internal_acc else (title_tok or accession)
        if not seqid:
            continue

        # 自分自身
        alias.setdefault(seqid, seqid)
        alias.setdefault(seqid.lower(), seqid)

        chrom = _infer_chrom_from_seqid_and_title(seqid, title or seqid)
        if chrom:
            alias.setdefault(chrom, seqid)
            alias.setdefault(chrom.lower(), seqid)
            m_num = re.match(r"^chr0*([0-9]+)$", chrom, flags=re.IGNORECASE)
            if m_num:
                alias.setdefault(str(int(m_num.group(1))), seqid)

    # 追加推定（best-effort）: cache にある entry→chr を取り込む
    if override and isinstance(override.get("mapping"), dict):
        alias.update(_build_alias_map_from_chrom_mapping(override.get("mapping") or {}))  # type: ignore[arg-type]

    return alias


def resolve_entry_for_local_db(db: str, entry: str) -> str:
    """
    UI 側で "chr1" のような表記を入力しても動くように、
    DB の実際の seqid へ変換する。
    """
    raw = (entry or "").strip()
    if not raw:
        return raw

    def _strip_location_suffix(s: str) -> str:
        """
        Ensembl 等の location 形式（例: `chr1:1,000-2,000`）を seqid のみに落とす。
        """
        t = (s or "").strip()
        if not t:
            return t
        m = re.match(
            r"^(.+?):\s*[0-9][0-9,]*(?:\s*(?:-|\.\.|–|—)\s*[0-9][0-9,]*)?(?:\s*\([^)]*\))?\s*$",
            t,
        )
        if m:
            return (m.group(1) or "").strip()
        return t

    raw_no_loc = _strip_location_suffix(raw)
    # ユーザー入力に defline 全体が入っていても、先頭 token（seqid）へ寄せる。
    token = raw_no_loc.split()[0] if raw_no_loc else raw_no_loc
    token = _strip_location_suffix(token).strip().rstrip(",;")
    token_unwrapped = _unwrap_seqid_wrapper(token)
    raw_unwrapped = _unwrap_seqid_wrapper(raw_no_loc)

    fallback = token_unwrapped or token or raw_unwrapped or raw_no_loc or raw
    db = normalize_db_prefix(db)
    table = _load_entry_alias_map(db)
    candidates: list[str] = []
    for cand in (raw, raw_no_loc, raw_unwrapped, token, token_unwrapped, fallback):
        c = (cand or "").strip()
        if c and c not in candidates:
            candidates.append(c)
    for c in candidates:
        c = c.rstrip(",;")
        if not c:
            continue
        key = _normalize_chr_alias(c)
        hit = (
            table.get(key)
            or table.get(key.lower())
            or table.get(c)
            or table.get(c.lower())
        )
        if hit:
            return hit
    return fallback


def get_db_chromosome_aliases(db: str) -> list[tuple[str, str]]:
    """
    DB 内で推定できる染色体ラベル（chr1 など）→実体 seqid の対応を返す。
    """
    db = normalize_db_prefix(db)
    mapping = _load_entry_alias_map(db)
    if not mapping:
        return []

    candidates: list[tuple[str, str]] = []
    for k, v in mapping.items():
        kk = (k or "").strip()
        if not kk.lower().startswith("chr"):
            continue
        # 正規化した "chrN" / "chrUn..." だけを採用（小文字 key も混ざるため）
        chrom = _normalize_chr_alias(kk)
        if not chrom.lower().startswith("chr"):
            continue
        candidates.append((chrom, v))

    # 重複除去（chr->entry が 1 対 1 の前提）
    uniq: dict[str, str] = {}
    for chrom, entry in candidates:
        uniq.setdefault(chrom, entry)

    def sort_key(item: tuple[str, str]) -> tuple[int, int, str]:
        chrom, _entry = item
        m = re.match(r"^chr0*([0-9]+)$", chrom, flags=re.IGNORECASE)
        if m:
            return (0, int(m.group(1)), chrom)
        m2 = re.match(r"^chrUn0*([0-9]+)", chrom, flags=re.IGNORECASE)
        if m2:
            return (1, int(m2.group(1)), chrom)
        return (2, 0, chrom)

    return sorted(uniq.items(), key=sort_key)


@lru_cache(maxsize=16)
def _load_db_entry_catalog(db: str) -> list[dict]:
    """
    blastdbcmd の defline から entry カタログを作る。

    - DB が巨大でも FASTA 全走査を避けたいので blastdbcmd -entry all を使う。
    - key は「defline の先頭トークン」（= BLAST の sseqid と一致することが多い）
    """
    proc = subprocess.run(
        [
            _blast_bin("blastdbcmd"),
            "-db",
            db,
            "-entry",
            "all",
            "-outfmt",
            "%a\t%l\t%t",
            "-long_seqids",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=_blast_timeout_sec(),
    )
    if proc.returncode != 0:
        raise BlastExecutionError(
            "blastdbcmd（entry 一覧）の実行に失敗しました（returncode=%s）。\nstderr:\n%s"
            % (proc.returncode, proc.stderr)
        )

    rows: list[dict] = []
    override = _load_chrom_alias_override(db)
    entry_to_chr: dict[str, str] = {}
    if override and isinstance(override.get("entry_to_chr"), dict):
        entry_to_chr = {k: v for k, v in (override.get("entry_to_chr") or {}).items() if isinstance(k, str) and isinstance(v, str)}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        accession = (parts[0] or "").strip()
        length_s = (parts[1] or "").strip()
        title = (parts[2] if len(parts) >= 3 else "").strip()
        title_tok = (title.split()[0] if title else "").strip()
        is_internal_acc = accession.upper().startswith("BL_ORD_ID:")
        entry = accession if accession and not is_internal_acc else (title_tok or accession)
        if not entry:
            continue
        try:
            length_val = int(length_s)
        except ValueError:
            length_val = None
        chrom = _infer_chrom_from_seqid_and_title(entry, title or entry)
        if not chrom and entry_to_chr:
            chrom = entry_to_chr.get(entry)
        rows.append(
            {
                "entry": entry,
                "title": title or entry,
                "chrom": chrom,
                "length": length_val,
            }
        )
    return rows


def _parse_blastdb_num_seqs(db: str) -> int | None:
    proc = subprocess.run(
        [
            _blast_bin("blastdbcmd"),
            "-db",
            db,
            "-info",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    # BLAST+ version differences:
    # - "Number of sequences: 7"
    # - "7 sequences; 3,845,180,674 total bases"
    m = re.search(r"Number of sequences:\s*([0-9][0-9,]*)", proc.stdout)
    if m:
        return int(m.group(1).replace(",", ""))
    m2 = re.search(r"^\s*([0-9][0-9,]*)\s+sequences\s*;", proc.stdout, flags=re.MULTILINE)
    if m2:
        return int(m2.group(1).replace(",", ""))
    return None


def _blastn_best_sseqid(query_seq: str, ref_db: str, *, task: str = "megablast") -> tuple[str | None, float | None]:
    """
    ref_db に対して query_seq を BLAST し、最良ヒットの sseqid と bitscore を返す（best effort）。
    """
    clean_seq = clean_query(query_seq)
    if not clean_seq:
        return None, None
    with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=True) as tmp_fa:
        tmp_fa.write(">q\n")
        tmp_fa.write(clean_seq + "\n")
        tmp_fa.flush()

        cmd = [
            _blast_bin("blastn"),
            "-task",
            task,
            "-query",
            tmp_fa.name,
            "-db",
            ref_db,
            "-outfmt",
            "6 sseqid bitscore",
            "-max_target_seqs",
            "1",
            "-evalue",
            "1e-20",
            "-num_threads",
            "1",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=_blast_timeout_sec())
        if proc.returncode != 0:
            return None, None
        line = (proc.stdout or "").strip().splitlines()[:1]
        if not line:
            return None, None
        parts = line[0].split("\t")
        if not parts:
            return None, None
        sseqid = (parts[0] or "").strip()
        score = None
        if len(parts) > 1:
            try:
                score = float(parts[1])
            except ValueError:
                score = None
        return sseqid, score


def build_chrom_alias_overrides(
    *,
    db: str,
    ref_db: str = "reference_v1",
    max_entries: int | None = 30,
    sample_bp: int = 2000,
    samples_per_entry: int = 6,
    job: object | None = None,
) -> dict[str, object]:
    """
    entry→chr の対応を参照DBで推定し、キャッシュファイルに保存する。

    戻り値: {"db":..., "ref_db":..., "mapping":{chr->entry}, "entry_to_chr":{entry->chr}}
    """
    # job は job_service.Job を想定（循環import回避のため型はobject）
    def _job_update(p: float, msg: str) -> None:
        if job is None:
            return
        try:
            job.update(progress=p, message=msg)  # type: ignore[attr-defined]
        except Exception:
            return

    def _job_cancel_check() -> None:
        if job is None:
            return
        try:
            job.raise_if_cancel_requested()  # type: ignore[attr-defined]
        except Exception:
            return

    db_norm = normalize_db_prefix(db)
    ref_norm = normalize_db_prefix(ref_db)
    db_name = Path(db_norm).name
    ref_name = Path(ref_norm).name

    _job_update(0.03, f"inspecting db={db_name}, ref={ref_name}")
    _job_cancel_check()

    # 既にキャッシュがあればそれを返す（即時）
    cached = _load_chrom_alias_override_for_ref(db_norm, ref_norm)
    if cached:
        _job_update(1.0, "cached")
        return cached

    num_seqs = _parse_blastdb_num_seqs(db_norm)
    if num_seqs is not None and num_seqs > 2000:
        raise BlastExecutionError(
            f"DB の entry 数が多すぎます（{num_seqs}）。この機能は染色体レベルのDB向けです。"
        )

    rows = _load_db_entry_catalog(db_norm)
    if not rows:
        raise BlastExecutionError("DB の entry 一覧が取得できませんでした。")

    # 長い順（染色体候補を優先）
    rows_sorted = sorted(rows, key=lambda r: int(r.get("length") or 0), reverse=True)
    if max_entries is not None:
        rows_sorted = rows_sorted[: max(1, int(max_entries))]

    # 既に chrom が推定できる entry はそれを採用
    entry_to_chr: dict[str, str] = {}
    mapping: dict[str, str] = {}

    # 参照DBの存在確認（軽く）
    if not _db_exists(Path(ref_norm)):
        raise BlastExecutionError(f"参照DB が見つかりません: {ref_norm}")

    total = max(1, len(rows_sorted))
    for idx, r in enumerate(rows_sorted):
        _job_cancel_check()
        entry = (r.get("entry") or "").strip()
        length = int(r.get("length") or 0)
        if not entry or length < max(500, sample_bp):
            continue

        chrom = r.get("chrom")
        if isinstance(chrom, str) and chrom.lower().startswith("chr"):
            chrom_norm = _normalize_chr_alias(chrom)
            entry_to_chr[entry] = chrom_norm
            mapping.setdefault(chrom_norm, entry)
            continue

        # サンプル位置（均等間隔）
        n_samples = max(1, int(samples_per_entry))
        span = max(1, length - sample_bp + 1)
        votes: dict[str, float] = {}
        for s_i in range(n_samples):
            _job_cancel_check()
            if n_samples == 1:
                start = max(1, (length // 2) - (sample_bp // 2))
            else:
                start = 1 + int((span - 1) * (s_i / (n_samples - 1)))
            end = min(length, start + sample_bp - 1)
            try:
                seq = fetch_sequence_local_db(db_norm, entry, start, end, "plus", max_len=50_000)
            except Exception:
                continue
            sseqid, score = _blastn_best_sseqid(seq, ref_norm, task="megablast")
            if not sseqid:
                continue
            chrom_guess = _infer_chrom_from_seqid_and_title(sseqid, sseqid)
            if not chrom_guess:
                continue
            votes[chrom_guess] = votes.get(chrom_guess, 0.0) + float(score or 1.0)

        if votes:
            best_chr = max(votes.items(), key=lambda kv: kv[1])[0]
            entry_to_chr[entry] = best_chr
            # 同じ chr に複数 entry が割り当たる場合は、先着（=長い順）を優先
            mapping.setdefault(best_chr, entry)

        # progress update
        _job_update(0.05 + 0.9 * ((idx + 1) / total), f"mapping: {idx + 1}/{total} entries")

    out = {
        "db": db_norm,
        "ref_db": ref_norm,
        "created_at": time.time(),
        "mapping": mapping,
        "entry_to_chr": entry_to_chr,
    }

    cache_dir = _chrom_alias_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = _chrom_alias_cache_path(db_norm, ref_norm)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    _invalidate_local_db_caches()
    _job_update(1.0, f"saved: {p.name}")
    return out


def save_inferred_chrom_alias_overrides(
    *,
    db: str,
    ref_db: str = "__inferred__",
) -> dict[str, object]:
    """
    blastdbcmd 由来の情報（accession/title）だけで推定できる chr→entry をキャッシュとして保存する。

    - BLAST を回さない
    - 目的は「起動/操作時の初回スキャンを避ける」こと
    """
    db_norm = normalize_db_prefix(db)
    ref_norm = normalize_db_prefix(ref_db)
    pairs = get_db_chromosome_aliases(db_norm)
    mapping: dict[str, str] = {k: v for k, v in pairs}
    entry_to_chr: dict[str, str] = {v: k for k, v in pairs}

    out = {
        "db": db_norm,
        "ref_db": ref_norm,
        "created_at": time.time(),
        "mode": "inferred",
        "mapping": mapping,
        "entry_to_chr": entry_to_chr,
    }

    cache_dir = _chrom_alias_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = _chrom_alias_cache_path(db_norm, ref_norm)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    _invalidate_local_db_caches()
    return out


def _list_local_nucl_db_prefixes() -> list[str]:
    """
    blast_databases_dir 配下の nucl DB prefix を列挙する（best-effort）。
    """
    base = blast_databases_dir()
    if not base.exists():
        return []
    prefixes: set[str] = set()
    for suf in (".nin", ".nsq", ".nhr", ".ndb"):
        for p in base.glob(f"*{suf}"):
            # suffix を落とす
            prefixes.add(str(p.with_suffix("")))
    return sorted(prefixes)


def bootstrap_chrom_aliases(
    *,
    ref_db: str = "reference_v1",
    max_dbs: int = 30,
    max_seqs: int = 80,
    job: object | None = None,
) -> dict[str, object]:
    """
    起動時などに、chr 情報が推定できないDBの chr→entry 推定を事前計算する。
    """
    def _job_update(p: float, msg: str) -> None:
        if job is None:
            return
        try:
            job.update(progress=p, message=msg)  # type: ignore[attr-defined]
        except Exception:
            return

    def _job_cancel_check() -> None:
        if job is None:
            return
        try:
            job.raise_if_cancel_requested()  # type: ignore[attr-defined]
        except Exception:
            return

    dbs = _list_local_nucl_db_prefixes()
    if not dbs:
        return {"ok": True, "total": 0, "built": 0, "skipped": 0, "errors": {}}

    ref_norm = normalize_db_prefix(ref_db)
    total = min(max(1, int(max_dbs)), len(dbs))
    built = 0
    skipped = 0
    errors: dict[str, str] = {}
    _job_update(0.02, f"bootstrap: scanning {len(dbs)} dbs (limit={total})")

    for i, db in enumerate(dbs[:total]):
        _job_cancel_check()
        name = Path(db).name
        _job_update(0.03 + 0.9 * (i / max(1, total)), f"bootstrap: {i+1}/{total} {name}")
        try:
            # 既に chr 推定できるならスキップ
            if get_db_chromosome_aliases(db):
                skipped += 1
                continue
            # すでに ref 付きキャッシュがあればスキップ
            if _load_chrom_alias_override_for_ref(db, ref_norm):
                skipped += 1
                continue
            nseq = _parse_blastdb_num_seqs(db)
            if nseq is not None and nseq > max_seqs:
                skipped += 1
                continue
            build_chrom_alias_overrides(
                db=db,
                ref_db=ref_norm,
                max_entries=30,
                sample_bp=2000,
                samples_per_entry=6,
                job=None,
            )
            built += 1
        except Exception as exc:
            errors[name] = str(exc)
            continue

    _job_update(1.0, f"bootstrap done: built={built}, skipped={skipped}, errors={len(errors)}")
    return {"ok": True, "total": total, "built": built, "skipped": skipped, "errors": errors}


def search_db_entries(db: str, q: str, limit: int = 50) -> list[dict]:
    """
    DB 内の entry を部分一致で検索する。
    """
    query = (q or "").strip().lower()
    if not query:
        return []
    db = normalize_db_prefix(db)
    limit = max(1, min(200, int(limit)))

    rows = _load_db_entry_catalog(db)
    matches: list[tuple[int, dict]] = []
    for r in rows:
        entry = (r.get("entry") or "").lower()
        title = (r.get("title") or "").lower()
        chrom = (r.get("chrom") or "").lower()
        if query not in entry and query not in title and (chrom and query not in chrom):
            continue
        score = 50
        if entry == query or chrom == query:
            score = 0
        elif entry.startswith(query) or chrom.startswith(query):
            score = 10
        elif f" {query}" in title:
            score = 20
        matches.append((score, r))

    matches.sort(key=lambda x: (x[0], x[1].get("entry") or ""))
    return [m[1] for m in matches[:limit]]


@dataclass
class BlastHit:
    """BLAST の 1 ヒット分の情報。"""

    qseqid: str
    sseqid: str
    pident: float
    length: int
    mismatch: int
    gapopen: int
    qstart: int
    qend: int
    sstart: int
    send: int
    evalue: float
    bitscore: float
    qseq: str | None = None
    sseq: str | None = None
    subject_title: str | None = None
    subject_chrom: str | None = None
    subject_length: int | None = None
    gene_ids: list[str] | None = None
    gene_names: list[str] | None = None


@dataclass
class BlastEquivalenceGateResult:
    enabled: bool = False
    passed: bool = True
    top1_match_rate: float | None = None
    top5_overlap_rate: float | None = None
    bitscore_median_delta: float | None = None
    bitscore_median_delta_ratio: float | None = None
    compared_queries: int = 0
    threshold_top1_min: float | None = None
    threshold_top5_overlap_min: float | None = None
    threshold_bitscore_median_max_delta: float | None = None
    threshold_bitscore_median_max_delta_ratio: float | None = None
    note: str | None = None


@dataclass
class BlastRunMeta:
    engine_requested: str = "blast"
    engine_executed: str = "blast"
    fallback_used: bool = False
    fallback_reason: str | None = None
    equivalence_gate: BlastEquivalenceGateResult | None = None


class BlastHitList(list):
    """
    list[BlastHit] に実行メタをぶら下げる軽量ラッパ。

    既存の list 互換挙動を維持しつつ、router 側でメタ情報を取得できるようにする。
    """

    def __init__(self, iterable: Iterable[BlastHit] = (), meta: BlastRunMeta | None = None):
        super().__init__(iterable)
        self.meta = meta


class BlastBatchHitList(list):
    """list[list[BlastHit]] 用のメタ付きラッパ。"""

    def __init__(self, iterable: Iterable[list[BlastHit]] = (), meta: BlastRunMeta | None = None):
        super().__init__(iterable)
        self.meta = meta


def _meta_default(engine_requested: str = "blast", engine_executed: str | None = None) -> BlastRunMeta:
    req = (engine_requested or "blast").strip().lower()
    if req not in {"blast", "cuda"}:
        req = "blast"
    exe = (engine_executed or req).strip().lower()
    if exe not in {"blast", "cuda"}:
        exe = "blast"
    return BlastRunMeta(engine_requested=req, engine_executed=exe)


def _equiv_gate_to_dict(g: BlastEquivalenceGateResult | None) -> dict[str, object] | None:
    if g is None:
        return None
    return {
        "enabled": bool(g.enabled),
        "passed": bool(g.passed),
        "top1_match_rate": g.top1_match_rate,
        "top5_overlap_rate": g.top5_overlap_rate,
        "bitscore_median_delta": g.bitscore_median_delta,
        "bitscore_median_delta_ratio": g.bitscore_median_delta_ratio,
        "compared_queries": int(g.compared_queries),
        "threshold_top1_min": g.threshold_top1_min,
        "threshold_top5_overlap_min": g.threshold_top5_overlap_min,
        "threshold_bitscore_median_max_delta": g.threshold_bitscore_median_max_delta,
        "threshold_bitscore_median_max_delta_ratio": g.threshold_bitscore_median_max_delta_ratio,
        "note": g.note,
    }


def run_meta_to_dict(meta: BlastRunMeta | None) -> dict[str, object]:
    m = meta or _meta_default()
    return {
        "engine_requested": m.engine_requested,
        "engine_executed": m.engine_executed,
        "fallback_used": bool(m.fallback_used),
        "fallback_reason": m.fallback_reason,
        "equivalence_gate": _equiv_gate_to_dict(m.equivalence_gate),
    }


def extract_run_meta(result: object, *, default_engine: str = "blast") -> BlastRunMeta:
    meta = getattr(result, "meta", None)
    if isinstance(meta, BlastRunMeta):
        return meta
    return _meta_default(default_engine)


@dataclass
class SubjectMeta:
    """ローカルDBの defline から抽出したメタデータ。"""

    title: str
    chrom: str | None
    length: int | None


@dataclass
class GeneFeature:
    """GFF3 由来の Gene 情報。"""

    seqid: str
    start: int
    end: int
    gene_id: str
    gene_name: str | None


def _normalize_gene_token(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.replace("gene:", "").replace("transcript:", "")
    s = s.split(":")[-1].split()[0]
    s = re.sub(r"-T\d+$", "", s, flags=re.IGNORECASE)
    m = re.match(r"^(.*)\.(\d+)$", s)
    if m:
        s = m.group(1)
    return s.strip()


@dataclass(frozen=True)
class _GeneIndexSeq:
    feats: list[GeneFeature]
    starts: list[int]
    ends: list[int]
    prefix_max_end: list[int]


@lru_cache(maxsize=8)
def _load_db_metadata(db_prefix: str) -> dict[str, SubjectMeta]:
    """
    makeblastdb に渡した元FASTAから seqid -> メタデータ の簡易マップを作る。

    - prefix.fa / prefix.fasta の defline を走査し、最初のトークンを key とする
    - defline に含まれる primary_assembly:... パターンから contig/長さを推定する
    """
    # Prefer blastdbcmd (DB index) over scanning huge FASTA files.
    # Many local DBs (e.g. reference_v2) symlink to multi-GB FASTA, so header scanning can dominate the first request.
    try:
        proc = subprocess.run(
            [
                _blast_bin("blastdbcmd"),
                "-db",
                db_prefix,
                "-entry",
                "all",
                "-outfmt",
                "%a\t%l\t%t",
                "-long_seqids",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_blast_timeout_sec(),
        )
    except FileNotFoundError:
        proc = None

    if proc is not None and proc.returncode == 0 and proc.stdout:
        meta: dict[str, SubjectMeta] = {}
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            accession = (parts[0] or "").strip()
            length_s = (parts[1] or "").strip()
            title = (parts[2] if len(parts) >= 3 else "").strip()
            title_tok = (title.split()[0] if title else "").strip()
            is_internal_acc = accession.upper().startswith("BL_ORD_ID:")
            seqid = accession if accession and not is_internal_acc else (title_tok or accession)
            if not seqid and not accession and not title_tok:
                continue
            try:
                length_val = int(length_s)
            except ValueError:
                length_val = None
            chrom_seed = title_tok if is_internal_acc and title_tok else (seqid or accession or title_tok)
            chrom = _infer_chrom_from_seqid_and_title(chrom_seed, title or chrom_seed) or chrom_seed
            subject = SubjectMeta(title=title or (seqid or accession or title_tok), chrom=chrom, length=length_val)
            for key in (accession, seqid, title_tok):
                k = (key or "").strip()
                if not k:
                    continue
                meta.setdefault(k, subject)
        if meta:
            return meta

    # Fallback: scan FASTA headers (best-effort)
    path = Path(db_prefix)
    fasta_candidates = [
        path.with_suffix(".fa"),
        path.with_suffix(".fasta"),
        path.with_suffix(".fna"),
        path.with_suffix(".faa"),
        path.with_suffix(".pep.fa"),
    ]
    fasta_path = next((p for p in fasta_candidates if p.exists()), None)
    if fasta_path is None:
        return {}

    meta: dict[str, SubjectMeta] = {}
    with fasta_path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line.startswith(">"):
                continue
            header = line[1:].strip()
            if not header:
                continue
            seqid, _, rest = header.partition(" ")
            title = rest.strip()
            chrom = _infer_chrom_from_seqid_and_title(seqid, header) or seqid
            meta[seqid] = SubjectMeta(title=title or seqid, chrom=chrom, length=None)
    return meta


def _annotate_local_hits(db_prefix: str, hits: list[BlastHit]) -> None:
    """ローカルDBの defline 由来のメタデータをヒットに付与する。"""
    meta = _load_db_metadata(db_prefix)
    if not meta:
        return
    for h in hits:
        # BLAST の sseqid には defline 全体が入ることがあるため、
        # 最初のトークン（FASTA の seqid）で照合する。
        raw_seqid = (h.sseqid or "").split()[0]
        seqid_candidates = [raw_seqid]
        unwrapped = _unwrap_seqid_wrapper(raw_seqid)
        if unwrapped and unwrapped not in seqid_candidates:
            seqid_candidates.append(unwrapped)

        m = None
        for seqid in seqid_candidates:
            m = meta.get(seqid)
            if m:
                break
        if not m:
            continue
        h.subject_title = m.title
        h.subject_chrom = m.chrom
        h.subject_length = m.length


def _parse_gff_attrs(attr_str: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for field in attr_str.split(";"):
        if not field:
            continue
        if "=" in field:
            k, v = field.split("=", 1)
            attrs[k.strip()] = v.strip()
    return attrs


def _strip_trailing_dot_number(token: str) -> str:
    """
    "X.1" のような末尾の isoform を除去する（途中の "." は保持する）。
    """
    return re.sub(r"\.(\d+)$", "", (token or "").strip())


@lru_cache(maxsize=2)
def _load_v1a_gene_index() -> dict[str, _GeneIndexSeq]:
    """
    互換性のため残しておくが、内部では _load_gene_index_by_path を呼ぶ。
    """
    gff_path, _alias = _gff_path_for_db(str(blast_databases_dir() / "reference_v1"))
    if not gff_path:
        return {}
    return _load_gene_index_by_path(gff_path)


@lru_cache(maxsize=4)
def _load_gene_index_by_path(gff_path: Path) -> dict[str, _GeneIndexSeq]:
    """
    任意の GFF3 から seqid ごとの GeneFeature 一覧を構築する。
    """
    if not gff_path.exists():
        return {}

    opener = gzip.open if gff_path.suffix.endswith("gz") else open
    gene_index: dict[str, list[GeneFeature]] = {}
    name_map: dict[str, str] = {}

    with opener(gff_path, "rt") as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            seqid, _source, ftype, start_s, end_s, _score, _strand, _phase, attrs_s = parts
            attrs = _parse_gff_attrs(attrs_s)
            if ftype == "gene":
                try:
                    start = int(start_s)
                    end = int(end_s)
                except ValueError:
                    continue
                raw_gid = attrs.get("ID") or attrs.get("gene_id") or attrs.get("gene")
                if not raw_gid:
                    continue
                gid_token = raw_gid.split(":")[-1]
                gid_token = gid_token.replace("gene-", "")
                gid = _strip_trailing_dot_number(gid_token)
                # まずは Name / gene / locus_tag を候補として保持
                gname = attrs.get("Name") or attrs.get("gene") or attrs.get("locus_tag")
                if gname:
                    name_map.setdefault(gid, gname)
                feat = GeneFeature(seqid=seqid, start=start, end=end, gene_id=gid, gene_name=None)
                gene_index.setdefault(seqid, []).append(feat)

    # gene_index には gene 行だけを入れておき、最後に name_map で gene_name を補完する
    out: dict[str, _GeneIndexSeq] = {}
    for seqid, feats in gene_index.items():
        for f in feats:
            if f.gene_id in name_map:
                f.gene_name = name_map[f.gene_id]
            elif f.gene_name is None:
                f.gene_name = f.gene_id
        feats.sort(key=lambda f: f.start)

        starts: list[int] = []
        ends: list[int] = []
        prefix_max_end: list[int] = []
        max_end = 0
        for f in feats:
            starts.append(f.start)
            ends.append(f.end)
            if f.end > max_end:
                max_end = f.end
            prefix_max_end.append(max_end)

        out[seqid] = _GeneIndexSeq(feats=feats, starts=starts, ends=ends, prefix_max_end=prefix_max_end)

    return out


@lru_cache(maxsize=1)
def _build_zw6_seqid_alias() -> dict[str, str]:
    """
    sample assembly (sample_assembly) 用に FASTA と GFF の seqid を突き合わせる。

    - FASTA: NC_066579.1 など（blast_databases_dir()/sample_assembly.fa）
    - GFF:   CM044345.1 など（genes.gff3 の ##sequence-region）
    長さが一致するものを 1対1 で対応付ける。
    """
    fasta_prefix = blast_databases_dir() / "sample_assembly"
    fai_path = fasta_prefix.with_suffix(".fa.fai")
    gff_path = blast_databases_dir() / "genes.gff3"
    if not fai_path.exists() or not gff_path.exists():
        return {}

    len_to_fa_ids: dict[int, list[str]] = {}
    with fai_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.strip().split("\t")
            if len(parts) < 2:
                continue
            name, length_s = parts[0], parts[1]
            try:
                length = int(length_s)
            except ValueError:
                continue
            len_to_fa_ids.setdefault(length, []).append(name)

    alias: dict[str, str] = {}
    with gff_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("##sequence-region "):
                continue
            # 例: ##sequence-region CM044345.1 1 463644500
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            gff_id = parts[1]
            try:
                length = int(parts[3])
            except ValueError:
                continue
            fa_ids = len_to_fa_ids.get(length)
            if not fa_ids or len(fa_ids) != 1:
                continue
            alias[fa_ids[0]] = gff_id
    return alias


def _annotate_genes(db_prefix: str, hits: list[BlastHit]) -> None:
    """
    データベースに応じて適切な GFF を用い、遺伝子名を付与する。
    """
    db_norm = normalize_db_prefix(db_prefix)
    gff_path, alias_map = _gff_path_for_db(db_norm)
    if not gff_path:
        return

    gene_index = _load_gene_index_by_path(gff_path)
    if not gene_index:
        return

    # 「重なっている gene」があればそれを、なければ近傍 gene を 1 つ採用する。
    max_distance = _nearest_gene_max_distance_bp(db_norm)

    def _find_overlaps(index: _GeneIndexSeq, start: int, end: int) -> list[GeneFeature]:
        if not index.feats:
            return []
        s = min(start, end)
        e = max(start, end)
        right = bisect_right(index.starts, e) - 1
        if right < 0:
            return []
        left = bisect_left(index.prefix_max_end, s, 0, right + 1)
        out: list[GeneFeature] = []
        for i in range(left, right + 1):
            f = index.feats[i]
            if f.start > e:
                break
            if f.end < s:
                continue
            out.append(f)
        return out

    for h in hits:
        raw_seqid = h.sseqid.split()[0]
        title_parts = (h.subject_title or "").split()
        title_seqid = title_parts[0] if title_parts else ""
        chrom_seqid = (h.subject_chrom or "").strip()
        key_candidates: list[str] = []
        for cand in (raw_seqid, title_seqid, chrom_seqid):
            c = (cand or "").strip()
            if not c:
                continue
            for k in (c, _unwrap_seqid_wrapper(c)):
                kk = (k or "").strip()
                if not kk:
                    continue
                key_candidates.append(alias_map.get(kk, kk))
                if kk.lower() != kk:
                    key_candidates.append(alias_map.get(kk.lower(), kk.lower()))
        # uniq (順序維持)
        dedup: list[str] = []
        seen: set[str] = set()
        for c in key_candidates:
            if not c:
                continue
            if c in seen:
                continue
            seen.add(c)
            dedup.append(c)

        idx: _GeneIndexSeq | None = None
        for k in dedup:
            idx = gene_index.get(k)
            if idx:
                break
        if not idx:
            continue
        q_start = min(h.sstart, h.send)
        q_end = max(h.sstart, h.send)
        overlapping = _find_overlaps(idx, q_start, q_end)

        targets: list[GeneFeature] = []
        if overlapping:
            targets = overlapping
        else:
            # 近傍 gene を探す（max_distance 以内）
            window_start = max(1, q_start - max_distance)
            window_end = q_end + max_distance
            candidates = _find_overlaps(idx, window_start, window_end)
            nearest: GeneFeature | None = None
            min_dist = max_distance + 1
            for f in candidates:
                if f.end < q_start:
                    dist = q_start - f.end
                elif f.start > q_end:
                    dist = f.start - q_end
                else:
                    dist = 0
                if dist <= 0:
                    continue
                if dist < min_dist:
                    min_dist = dist
                    nearest = f
            if nearest and min_dist <= max_distance:
                targets = [nearest]

        if not targets:
            continue

        ids = sorted({f.gene_id for f in targets})
        names = sorted({f.gene_name for f in targets if f.gene_name})
        h.gene_ids = ids
        h.gene_names = names or None


def _nearest_gene_max_distance_bp(db_prefix: str) -> int:
    """
    非重複ヒットに対して「近傍 gene」を採用する距離上限（bp）を返す。

    - 全体既定: BLAST_GENE_NEAREST_MAX_DISTANCE_BP (default: 10000)
    - sample_assembly 既定: BLAST_GENE_NEAREST_MAX_DISTANCE_BP_sample_assembly
      未指定時は max(global, 100000)
    """
    db_norm = normalize_db_prefix(db_prefix)
    name = Path(db_norm).name.lower()

    global_limit = max(1, _parse_env_int("BLAST_GENE_NEAREST_MAX_DISTANCE_BP", 10_000))
    if "sample_assembly" not in name:
        return global_limit

    zw6_default = max(global_limit, 100_000)
    zw6_limit = _parse_env_int("BLAST_GENE_NEAREST_MAX_DISTANCE_BP_sample_assembly", zw6_default)
    return max(1, zw6_limit)


def _gff_path_for_db(db_prefix: str) -> tuple[Path | None, dict[str, str]]:
    """
    DB に対応する GFF3 のパスと、必要なら seqid の alias map を返す。

    - sample assembly は FASTA と GFF で seqid が一致しないため alias map を利用する
    """
    db_prefix = normalize_db_prefix(db_prefix)
    name = Path(db_prefix).name.lower()
    is_v1a = "reference_v1" in name
    is_zw6 = "sample_assembly" in name
    is_v2 = "reference_v2" in name
    p = Path(db_prefix)
    sidecar_candidates = [p.with_suffix(suffix) for suffix in (".gff3.gz", ".gff3", ".gff.gz", ".gff")]

    def _pick_existing(candidates: list[Path]) -> Path | None:
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    gff_path: Path | None = None
    if is_v1a:
        gff_path = _pick_existing(
            sidecar_candidates
            + [
                blast_databases_dir() / "reference_v1.gff3.gz",
                blast_databases_dir() / "reference_v1.gff3",
                workbench_tmp_dir() / "arabidopsis_thaliana.arabidopsis_thaliana_v1a.62.gff3.gz",
                workbench_tmp_dir() / "arabidopsis_thaliana.arabidopsis_thaliana_v1a.62.gff3",
            ]
        )
    elif is_zw6:
        gff_path = _pick_existing(
            [blast_databases_dir() / "genes.gff3"]
            + sidecar_candidates
        )
    elif is_v2:
        gff_path = _pick_existing(
            sidecar_candidates
            + [
                blast_databases_dir() / "dataverse_files" / "reference_V2_ANNOTATION_1.gff3",
                blast_databases_dir() / "reference_v2.gff3.gz",
                blast_databases_dir() / "reference_v2.gff3",
            ]
        )
    else:
        # 汎用: DB プレフィックスと同名の GFF が隣にある場合はそれを利用する
        # 例: ~/sequence_workbench/blast_databases/accession_e (DB) + ~/sequence_workbench/blast_databases/accession_e.gff3
        gff_path = _pick_existing(sidecar_candidates)

    alias_map: dict[str, str] = {}
    if is_zw6:
        alias_map = _build_zw6_seqid_alias()
    return gff_path if gff_path and gff_path.exists() else None, alias_map


def guess_db_gff_path(db_prefix: str) -> Path | None:
    """
    Best-effort: return a local GFF3 path for the given DB prefix if available.

    This is used by UI helpers (e.g. local DB list) to show whether annotations exist.
    """
    db_norm = normalize_db_prefix(db_prefix)
    gff_path, _alias = _gff_path_for_db(db_norm)
    return gff_path


@lru_cache(maxsize=16)
def _gene_lookup_by_gff_key(gff_key: str) -> dict[str, GeneFeature]:
    """
    GFF3 から gene_id/gene_name で引ける簡易辞書を構築する（key は lower）。
    """
    path_s, _, _mtime_s = gff_key.partition("::")
    gff_path = Path(path_s)
    idx = _load_gene_index_by_path(gff_path)
    out: dict[str, GeneFeature] = {}
    for _seqid, seq in idx.items():
        for f in seq.feats:
            gid = _normalize_gene_token(f.gene_id)
            if gid:
                out.setdefault(gid.lower(), f)
            if f.gene_name:
                gname = _normalize_gene_token(f.gene_name)
                if gname:
                    out.setdefault(gname.lower(), f)
    return out


def get_gene_locations(db_prefix: str, gene_ids: list[str]) -> list[dict[str, object]]:
    """
    DB の GFF3 から gene の染色体/座標を best-effort で返す。
    """
    db_norm = normalize_db_prefix(db_prefix)
    gff_path, alias_map = _gff_path_for_db(db_norm)
    meta = _load_db_metadata(db_norm)

    inv_alias: dict[str, str] = {}
    if alias_map:
        for fa_id, gff_id in alias_map.items():
            if fa_id and gff_id and gff_id not in inv_alias:
                inv_alias[gff_id] = fa_id

    lookup: dict[str, GeneFeature] = {}
    if gff_path and gff_path.exists():
        try:
            st = gff_path.stat()
            key = f"{gff_path}::{int(st.st_mtime)}"
            lookup = _gene_lookup_by_gff_key(key)
        except Exception:
            lookup = {}

    out: list[dict[str, object]] = []
    for raw in gene_ids:
        norm = _normalize_gene_token(raw)
        feat = lookup.get(norm.lower()) if norm and lookup else None
        if feat is None:
            out.append(
                {
                    "db": Path(db_norm).name,
                    "input": raw,
                    "normalized": norm,
                    "found": False,
                    "gene_id": None,
                    "gene_name": None,
                    "seqid": None,
                    "chrom": None,
                    "start": None,
                    "end": None,
                }
            )
            continue

        seqid = feat.seqid
        meta_key = seqid
        if meta_key not in meta and seqid in inv_alias:
            meta_key = inv_alias[seqid]
        m = meta.get(meta_key)
        chrom = m.chrom if m and m.chrom else seqid

        out.append(
            {
                "db": Path(db_norm).name,
                "input": raw,
                "normalized": norm,
                "found": True,
                "gene_id": feat.gene_id,
                "gene_name": feat.gene_name,
                "seqid": seqid,
                "chrom": chrom,
                "start": int(feat.start),
                "end": int(feat.end),
            }
        )
    return out


def _merge_ranges_1based(ranges: list[tuple[int, int]], *, merge_gap: int = 0) -> list[dict[str, int]]:
    cleaned = (
        [(min(s, e), max(s, e)) for s, e in ranges if isinstance(s, int) and isinstance(e, int) and s >= 1 and e >= 1]
    )
    cleaned.sort(key=lambda r: r[0])

    merged: list[list[int]] = []
    for s, e in cleaned:
        if not merged:
            merged.append([s, e])
            continue
        last = merged[-1]
        if s <= last[1] + merge_gap + 1:
            last[1] = max(last[1], e)
        else:
            merged.append([s, e])

    return [{"start": s, "end": e} for s, e in merged]


def _find_overlapping_genes(index: _GeneIndexSeq, start: int, end: int) -> list[GeneFeature]:
    if not index.feats:
        return []
    s = min(start, end)
    e = max(start, end)
    right = bisect_right(index.starts, e) - 1
    if right < 0:
        return []
    left = bisect_left(index.prefix_max_end, s, 0, right + 1)
    out: list[GeneFeature] = []
    for i in range(left, right + 1):
        f = index.feats[i]
        if f.start > e:
            break
        if f.end < s:
            continue
        out.append(f)
    return out


def _collect_gene_subfeatures_from_gff(
    gff_path: Path,
    seqid: str,
    *,
    gene_id: str,
    gene_name: str | None,
    gene_start: int,
    gene_end: int,
) -> dict[str, object]:
    """
    GFF3 を走査して、指定 gene の exon/CDS を best-effort で収集する。

    - transcript を跨いだ重複はマージして返す（union 表示向け）
    """
    opener = gzip.open if gff_path.suffix.endswith("gz") else open

    tokens_raw = [gene_name or "", gene_id or ""]
    tokens: list[str] = []
    for t in tokens_raw:
        t2 = (t or "").strip()
        if not t2:
            continue
        tokens.append(t2)
        tokens.append(re.sub(r"\.\d+$", "", t2))
        tokens.append(re.sub(r"-T\d+$", "", t2))
        tokens.append(t2.split("-")[0])
    tokens = list(dict.fromkeys([t for t in tokens if t]))
    # 部分一致（GENE1g... の prefix が被る等）を避けるため、単語境界ベースでマッチする。
    token_regex = (
        re.compile("|".join([rf"\b{re.escape(t)}\b" for t in tokens]), flags=re.IGNORECASE)
        if tokens
        else None
    )

    def matches_any(val: str | None) -> bool:
        if not token_regex:
            return False
        if not val:
            return False
        return bool(token_regex.search(val))

    def attrs_match(attrs: dict[str, str]) -> bool:
        for key in ("ID", "Parent", "gene_id", "gene", "Name", "locus_tag", "orig_transcript_id", "transcript_id"):
            if matches_any(attrs.get(key)):
                return True
        return False

    gene_raw_ids: set[str] = set()
    transcript_ids: set[str] = set()
    exons: list[tuple[int, int]] = []
    cds: list[tuple[int, int]] = []
    strand: int = 1
    biotype: str | None = None
    found_gene = False
    in_seqid = False

    with opener(gff_path, "rt", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            seqid_line, _source, ftype, start_s, end_s, _score, strand_s, _phase, attrs_s = parts

            if seqid_line != seqid:
                if in_seqid:
                    break
                continue
            in_seqid = True

            try:
                s = int(start_s)
                e = int(end_s)
            except ValueError:
                continue

            if max(s, e) < gene_start:
                continue
            if min(s, e) > gene_end and found_gene:
                break

            attrs = _parse_gff_attrs(attrs_s)

            if ftype == "gene":
                if not attrs_match(attrs):
                    continue
                found_gene = True
                gid = (attrs.get("ID") or attrs.get("gene_id") or attrs.get("gene") or attrs.get("Name") or "").strip()
                if gid:
                    gene_raw_ids.add(gid.split(",")[0])
                biotype = biotype or attrs.get("biotype") or attrs.get("gene_biotype") or attrs.get("gbkey")
                strand = 1 if strand_s == "+" else -1
                continue

            parent = (attrs.get("Parent") or "").strip()
            parents = [p for p in parent.split(",") if p] if parent else []

            if ftype in {"mRNA", "transcript"}:
                is_child = False
                if parents and gene_raw_ids and any(p in gene_raw_ids for p in parents):
                    is_child = True
                elif attrs_match(attrs):
                    is_child = True
                if not is_child:
                    continue
                tid = (attrs.get("ID") or "").strip()
                if tid:
                    transcript_ids.add(tid.split(",")[0])
                continue

            if ftype in {"exon", "CDS"}:
                ok = False
                if transcript_ids and parents and any(p in transcript_ids for p in parents):
                    ok = True
                elif attrs_match(attrs):
                    ok = True
                elif parents and gene_raw_ids and any(p in gene_raw_ids for p in parents):
                    ok = True
                if not ok:
                    continue
                if ftype == "exon":
                    exons.append((s, e))
                else:
                    cds.append((s, e))

    return {
        "strand": strand,
        "biotype": biotype,
        "exons": _merge_ranges_1based(exons),
        "cds": _merge_ranges_1based(cds),
    }


@lru_cache(maxsize=256)
def _collect_gene_subfeatures_from_gff_cached(
    gff_path_str: str,
    seqid: str,
    gene_id: str,
    gene_name: str | None,
    gene_start: int,
    gene_end: int,
) -> dict[str, object]:
    return _collect_gene_subfeatures_from_gff(
        Path(gff_path_str),
        seqid,
        gene_id=gene_id,
        gene_name=gene_name,
        gene_start=gene_start,
        gene_end=gene_end,
    )


def get_region_gene_model(
    *,
    db: str,
    entry: str,
    start: int,
    end: int,
    gene_hint: str | None = None,
    max_genes: int = 3,
) -> dict[str, object]:
    """
    ローカル GFF3 を使って、指定領域に重なる gene の exon/CDS を返す。

    返却値は API の response_model に合わせた dict。
    """
    db_norm = normalize_db_prefix(db)
    gff_path, alias_map = _gff_path_for_db(db_norm)
    if not gff_path:
        return {"db": db, "entry": entry, "start": start, "end": end, "genes": []}

    resolved_entry = resolve_entry_for_local_db(db_norm, entry)
    gff_seqid = alias_map.get(resolved_entry, resolved_entry)

    gene_index = _load_gene_index_by_path(gff_path)
    idx = gene_index.get(gff_seqid)
    if not idx:
        return {"db": db, "entry": entry, "start": start, "end": end, "genes": []}

    s = min(start, end)
    e = max(start, end)
    overlaps = _find_overlapping_genes(idx, s, e)

    candidates = overlaps
    if not candidates:
        # 近傍 gene を 1 つ拾う（プライマーが gene の外側に出ている場合の救済）
        max_distance = 10_000
        window_start = max(1, s - max_distance)
        window_end = e + max_distance
        nearby = _find_overlapping_genes(idx, window_start, window_end)
        nearest: GeneFeature | None = None
        min_dist = max_distance + 1
        for f in nearby:
            if f.end < s:
                dist = s - f.end
            elif f.start > e:
                dist = f.start - e
            else:
                dist = 0
            if dist <= 0:
                continue
            if dist < min_dist:
                min_dist = dist
                nearest = f
        candidates = [nearest] if nearest and min_dist <= max_distance else []

    hint = (gene_hint or "").strip().lower()
    hint_norm = re.sub(r"\.\d+$", "", hint)

    def matches_hint(f: GeneFeature) -> bool:
        if not hint_norm:
            return False
        g1 = (f.gene_name or "").lower()
        g2 = (f.gene_id or "").lower()
        return hint_norm in g1 or hint_norm in g2 or g1 in hint_norm or g2 in hint_norm

    def overlap_len(f: GeneFeature) -> int:
        return max(0, min(f.end, e) - max(f.start, s) + 1)

    candidates.sort(
        key=lambda f: (
            0 if matches_hint(f) else 1,
            -overlap_len(f),
            -(f.end - f.start + 1),
            (f.gene_name or f.gene_id),
        )
    )
    candidates = candidates[: max(0, int(max_genes))]

    genes_out: list[dict[str, object]] = []
    for f in candidates:
        sub = _collect_gene_subfeatures_from_gff_cached(
            str(gff_path),
            gff_seqid,
            f.gene_id,
            f.gene_name,
            f.start,
            f.end,
        )
        genes_out.append(
            {
                "seqid": gff_seqid,
                "gene_id": f.gene_id,
                "gene_name": f.gene_name,
                "biotype": sub.get("biotype"),
                "strand": sub.get("strand") or 1,
                "start": f.start,
                "end": f.end,
                "exons": sub.get("exons") or [],
                "cds": sub.get("cds") or [],
            }
        )

    return {
        "db": Path(db_norm).name,
        "entry": resolved_entry,
        "start": start,
        "end": end,
        "genes": genes_out,
    }


def run_blastn_sync(
    sequence: str,
    db: str,
    task: str = "blastn",
    evalue: float = 1e-5,
    max_target_seqs: int = 25,
    num_threads: int | None = None,
    max_hsps: int | None = None,
    local_mode: str | None = None,
    engine: str = "blast",
) -> List[BlastHit]:
    """
    blastn を同期的に実行し、BlastHit のリストを返す。

    - `db` は makeblastdb で作成したデータベースのプレフィックスパス。
    - 出力形式は outfmt=6（タブ区切り）を利用する。
    """
    logger = logging.getLogger(__name__)
    engine_norm = (engine or "blast").strip().lower()
    if engine_norm not in {"blast", "cuda"}:
        engine_norm = "blast"
    meta = _meta_default(engine_norm)

    resolved_db, _label, resolved_mode = resolve_local_db_plan(db, local_mode)
    db_prefix = Path(resolved_db)

    def clean_query(raw: str) -> str:
        body = "".join(
            line.strip()
            for line in (raw or "").splitlines()
            if line.strip() and not line.strip().startswith(">")
        )
        return re.sub(r"[^A-Za-z]", "", body).upper()

    clean_seq = clean_query(sequence)
    effective_task = _effective_blastn_task_for_sequences(task, [clean_seq])

    if not _db_exists(db_prefix):
        raise BlastExecutionError(f"BLAST データベースが見つかりません: {resolved_db}")

    if engine_norm == "cuda":
        quarantine_reason = _cuda_quarantine_reason_for_workload(resolved_db, effective_task, [clean_seq])
        if quarantine_reason:
            logger.info(
                "cuda skipped by quarantine (single): db=%s task=%s reason=%s",
                resolved_db,
                effective_task,
                quarantine_reason,
            )
            cpu_fallback = run_blastn_sync(
                sequence=sequence,
                db=db,
                task=task,
                evalue=evalue,
                max_target_seqs=max_target_seqs,
                num_threads=num_threads,
                max_hsps=max_hsps,
                local_mode="cpu",
                engine="blast",
            )
            meta.engine_executed = "blast"
            meta.fallback_used = True
            meta.fallback_reason = f"cuda quarantined: {quarantine_reason}"
            return BlastHitList(list(cpu_fallback), meta=meta)

        skip_reason = _cuda_skip_reason_for_workload(effective_task, [clean_seq])
        if skip_reason:
            logger.info(
                "cuda skipped by workload heuristic (single): db=%s task=%s reason=%s",
                resolved_db,
                effective_task,
                skip_reason,
            )
            cpu_fallback = run_blastn_sync(
                sequence=sequence,
                db=db,
                task=task,
                evalue=evalue,
                max_target_seqs=max_target_seqs,
                num_threads=num_threads,
                max_hsps=max_hsps,
                local_mode="cpu",
                engine="blast",
            )
            meta.engine_executed = "blast"
            meta.fallback_used = True
            meta.fallback_reason = f"cuda skipped: {skip_reason}"
            return BlastHitList(list(cpu_fallback), meta=meta)

        if not CUDA_AVAILABLE:
            if _cuda_strict_mode():
                raise BlastExecutionError(
                    f"CUDA-BLASTN は利用不可です: {CUDA_UNAVAILABLE_REASON or 'binary unavailable'}"
                )
            cpu_fallback = run_blastn_sync(
                sequence=sequence,
                db=db,
                task=task,
                evalue=evalue,
                max_target_seqs=max_target_seqs,
                num_threads=num_threads,
                max_hsps=max_hsps,
                local_mode="cpu",
                engine="blast",
            )
            meta.engine_executed = "blast"
            meta.fallback_used = True
            meta.fallback_reason = f"cuda unavailable: {CUDA_UNAVAILABLE_REASON or 'binary unavailable'}"
            return BlastHitList(list(cpu_fallback), meta=meta)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=False) as tmp:
            tmp.write(">query\n")
            tmp.write(clean_seq + "\n")
            tmp_path = tmp.name
        try:
            try:
                gpu_hits = _run_cuda_blastn(
                    Path(tmp_path),
                    resolved_db,
                    evalue,
                    max_target_seqs,
                    task=effective_task,
                    max_hsps=max_hsps,
                    query_sequences=[clean_seq],
                )
                gpu_hits.sort(key=lambda h: (-h.bitscore, -h.pident))
                _annotate_local_hits(resolved_db, gpu_hits)
                _annotate_genes(resolved_db, gpu_hits)
            except BlastExecutionError as exc:
                if _cuda_strict_mode():
                    raise
                _remember_cuda_quarantine(
                    resolved_db,
                    effective_task,
                    [clean_seq],
                    f"cuda execution failed: {exc}",
                )
                logger.warning(
                    "cuda-blast failed; fallback to BLAST+ (db=%s): %s",
                    resolved_db,
                    exc,
                )
                cpu_fallback = run_blastn_sync(
                    sequence=sequence,
                    db=db,
                    task=task,
                    evalue=evalue,
                    max_target_seqs=max_target_seqs,
                    num_threads=num_threads,
                    max_hsps=max_hsps,
                    local_mode="cpu",
                    engine="blast",
                )
                meta.engine_executed = "blast"
                meta.fallback_used = True
                meta.fallback_reason = f"cuda execution failed: {exc}"
                return BlastHitList(list(cpu_fallback), meta=meta)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        gate_enabled = _blast_cuda_gate_enabled()
        need_shadow = (not gate_enabled) and (_blast_cuda_shadow_rate() > 0.0)
        if gate_enabled or need_shadow:
            cpu_reference = run_blastn_sync(
                sequence=sequence,
                db=db,
                task=task,
                evalue=evalue,
                max_target_seqs=max_target_seqs,
                num_threads=num_threads,
                max_hsps=max_hsps,
                local_mode="cpu",
                engine="blast",
            )
            cpu_hits = list(cpu_reference)
            gate = _evaluate_cuda_equivalence([gpu_hits], [cpu_hits])
            if not gate_enabled:
                gate.enabled = False
                gate.note = f"shadow_compare: {gate.note or ''}".strip()
            meta.equivalence_gate = gate
            if gate_enabled and not gate.passed:
                logger.warning(
                    "cuda equivalence gate failed (single): %s; fallback to CPU",
                    gate.note or "threshold not met",
                )
                _remember_cuda_quarantine(
                    resolved_db,
                    effective_task,
                    [clean_seq],
                    f"equivalence gate failed: {gate.note or 'threshold not met'}",
                )
                meta.engine_executed = "blast"
                meta.fallback_used = True
                meta.fallback_reason = f"equivalence gate failed: {gate.note or 'threshold not met'}"
                return BlastHitList(cpu_hits, meta=meta)
            if not gate_enabled:
                logger.info(
                    "cuda shadow compare (single): top1=%.3f top5=%.3f bitscore_delta=%s bitscore_delta_ratio=%s passed=%s",
                    gate.top1_match_rate or 0.0,
                    gate.top5_overlap_rate or 0.0,
                    f"{gate.bitscore_median_delta:.3f}" if gate.bitscore_median_delta is not None else "n/a",
                    f"{(gate.bitscore_median_delta_ratio or 0.0) * 100.0:.3f}%"
                    if gate.bitscore_median_delta_ratio is not None
                    else "n/a",
                    gate.passed,
                )

        meta.engine_executed = "cuda"
        return BlastHitList(gpu_hits, meta=meta)

    # 一時ファイルにクエリ配列を FASTA 形式で書き出す
    with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=True) as tmp_fa:
        tmp_fa.write(">query\n")
        tmp_fa.write(clean_seq + "\n")
        tmp_fa.flush()

        threads = clamp_blast_threads(num_threads, parallel_jobs=1)
        start_time = time.perf_counter()
        task_norm = (effective_task or "").lower()
        seqidlist_path: Path | None = None
        gblastn_path = Path(GBLASTN_BLASTN) if GBLASTN_BLASTN else None
        use_gblastn = resolved_mode == "gpu" and gblastn_path is not None and gblastn_path.exists()

        if resolved_mode == "gpu" and not use_gblastn:
            prefilter = _maybe_run_gpu_prefilter(
                Path(tmp_fa.name),
                db_prefix,
                [clean_seq],
                effective_task,
            )
            if prefilter is not None:
                seqidlist_path, _seqid_count = prefilter

        blast_bin = str(gblastn_path) if use_gblastn and gblastn_path is not None else _blast_bin("blastn")
        cmd = [
            blast_bin,
            "-task",
            effective_task,
            "-query",
            tmp_fa.name,
            "-db",
            resolved_db,
            "-outfmt",
            "6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore",
            "-evalue",
            str(evalue),
            "-max_target_seqs",
            str(max_target_seqs),
            "-num_threads",
            str(threads),
        ]
        if max_hsps:
            cmd.extend(["-max_hsps", str(max_hsps)])
        if task_norm == "megablast" and _has_megablast_index(db_prefix):
            max_bp = _parse_env_int("BLAST_USE_INDEX_MAX_QUERY_BP", 5000)
            if max_bp > 0 and len(clean_seq) <= max_bp:
                cmd.extend(["-use_index", "true"])
        if resolved_mode == "gpu":
            dbsize = _get_db_total_bases(str(db_prefix))
            if dbsize:
                cmd.extend(["-dbsize", str(dbsize)])
            if seqidlist_path is not None:
                cmd.extend(["-seqidlist", str(seqidlist_path)])
        if use_gblastn:
            cmd.extend(["-use_gpu", "true"])

        timeout_sec = _blast_timeout_sec()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise BlastExecutionError(
                f"blastn がタイムアウトしました（{timeout_sec}s）。"
                "クエリ数/DB数/ヒット数を減らすか、BLAST_SUBPROCESS_TIMEOUT_SEC を増やしてください。"
            ) from exc

        elapsed = time.perf_counter() - start_time
        if proc.returncode != 0:
            _raise_process_error(cmd, proc.returncode, proc.stderr or "")

    hits: List[BlastHit] = []
    max_total_hits = _blast_single_max_total_hits()
    truncated = False
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 12:
            continue
        if len(hits) >= max_total_hits:
            truncated = True
            break
        hits.append(
            BlastHit(
                qseqid=parts[0],
                sseqid=parts[1],
                pident=float(parts[2]),
                length=int(parts[3]),
                mismatch=int(parts[4]),
                gapopen=int(parts[5]),
                qstart=int(parts[6]),
                qend=int(parts[7]),
                sstart=int(parts[8]),
                send=int(parts[9]),
                evalue=float(parts[10]),
                bitscore=float(parts[11]),
            )
        )

    hits.sort(key=lambda h: (-h.bitscore, -h.pident))
    _annotate_local_hits(resolved_db, hits)
    _annotate_genes(resolved_db, hits)
    if truncated:
        logger.warning(
            "blastn output truncated: db=%s max_total_hits=%s (tune BLAST_SINGLE_MAX_TOTAL_HITS if needed)",
            resolved_db,
            max_total_hits,
        )

    logger.info(
        "blastn finished db=%s task=%s evalue=%s max_target_seqs=%s max_hsps=%s threads=%s hits=%s elapsed=%.3fs",
        resolved_db,
        effective_task,
        evalue,
        max_target_seqs,
        max_hsps,
        threads,
        len(hits),
        elapsed,
    )

    meta.engine_executed = "blast"
    return BlastHitList(hits, meta=meta)


def run_blast_with_alignment_sync(
    program: str,
    sequence: str,
    db: str,
    task: str = "blastn",
    evalue: float = 1e-5,
    max_target_seqs: int = 25,
    num_threads: int | None = None,
    max_hsps: int | None = None,
    local_mode: str | None = None,
    engine: str = "blast",
) -> List[BlastHit]:
    """
    BLAST を同期的に実行し、qseq/sseq（ギャップ込みのアラインメント配列）付きで BlastHit を返す。

    - 出力形式は outfmt=6（タブ区切り）+ qseq/sseq を利用する。
    - 現状、engine="cuda" はアラインメント配列の取得に対応していないためエラーとする。
    - program は "blastn" / "blastp" をサポートする。
    """
    logger = logging.getLogger(__name__)
    engine_norm = (engine or "blast").strip().lower()
    if engine_norm == "cuda":
        raise BlastExecutionError('BLAST-OR 形式は engine="cuda" に対応していません。engine="blast" を指定してください。')

    program_norm = (program or "blastn").strip().lower()
    if program_norm not in {"blastn", "blastp"}:
        raise BlastExecutionError('program は "blastn" / "blastp" のいずれかを指定してください。')

    if program_norm == "blastp" and (not task or (task or "").lower() in {"blastn", "blastn-short", "megablast", "dc-megablast"}):
        task = "blastp"

    resolved_db, _label, resolved_mode = resolve_local_db_plan(db, local_mode)
    db_prefix = Path(resolved_db)

    def clean_query(raw: str) -> str:
        body = "".join(
            line.strip()
            for line in (raw or "").splitlines()
            if line.strip() and not line.strip().startswith(">")
        )
        return re.sub(r"[^A-Za-z]", "", body).upper()

    db_type = "prot" if program_norm == "blastp" else "nucl"
    if not _db_exists(db_prefix, db_type=db_type):
        raise BlastExecutionError(f"BLAST データベースが見つかりません: {resolved_db}")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=True) as tmp_fa:
        clean_seq = clean_query(sequence)
        effective_task = (
            _effective_blastn_task_for_sequences(task, [clean_seq])
            if program_norm == "blastn"
            else task
        )
        tmp_fa.write(">query\n")
        tmp_fa.write(clean_seq + "\n")
        tmp_fa.flush()

        threads = clamp_blast_threads(num_threads, parallel_jobs=1)
        start_time = time.perf_counter()
        task_norm = (effective_task or "").lower()
        seqidlist_path: Path | None = None
        if program_norm == "blastn" and resolved_mode == "gpu":
            prefilter = _maybe_run_gpu_prefilter(
                Path(tmp_fa.name),
                db_prefix,
                [clean_seq],
                effective_task,
            )
            if prefilter is not None:
                seqidlist_path, _seqid_count = prefilter

        cmd = [
            _blast_bin(program_norm),
            "-task",
            effective_task,
            "-query",
            tmp_fa.name,
            "-db",
            resolved_db,
            "-outfmt",
            "6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qseq sseq",
            "-evalue",
            str(evalue),
            "-max_target_seqs",
            str(max_target_seqs),
            "-num_threads",
            str(threads),
        ]
        # NOTE: BLAST+ versions differ in supported mt_mode values. Avoid forcing a mode here and rely on defaults.
        if max_hsps:
            cmd.extend(["-max_hsps", str(max_hsps)])
        if program_norm == "blastn" and task_norm == "megablast" and _has_megablast_index(db_prefix):
            max_bp = _parse_env_int("BLAST_USE_INDEX_MAX_QUERY_BP", 5000)
            if max_bp > 0 and len(clean_seq) <= max_bp:
                cmd.extend(["-use_index", "true"])
        if program_norm == "blastn" and resolved_mode == "gpu":
            dbsize = _get_db_total_bases(str(db_prefix))
            if dbsize:
                cmd.extend(["-dbsize", str(dbsize)])
            if seqidlist_path is not None:
                cmd.extend(["-seqidlist", str(seqidlist_path)])

        timeout_sec = _blast_timeout_sec()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise BlastExecutionError(
                f"{program_norm} がタイムアウトしました（{timeout_sec}s）。"
                "条件を軽くするか、BLAST_SUBPROCESS_TIMEOUT_SEC を増やしてください。"
            ) from exc

        elapsed = time.perf_counter() - start_time

        if proc.returncode != 0:
            _raise_process_error(cmd, proc.returncode, proc.stderr or "")

    hits: List[BlastHit] = []
    max_total_hits = _blast_alignment_max_total_hits()
    truncated = False
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 14:
            continue
        if len(hits) >= max_total_hits:
            truncated = True
            break
        hits.append(
            BlastHit(
                qseqid=parts[0],
                sseqid=parts[1],
                pident=float(parts[2]),
                length=int(parts[3]),
                mismatch=int(parts[4]),
                gapopen=int(parts[5]),
                qstart=int(parts[6]),
                qend=int(parts[7]),
                sstart=int(parts[8]),
                send=int(parts[9]),
                evalue=float(parts[10]),
                bitscore=float(parts[11]),
                qseq=parts[12] or "",
                sseq=parts[13] or "",
            )
        )

    hits.sort(key=lambda h: (-h.bitscore, -h.pident))
    _annotate_local_hits(resolved_db, hits)
    if program_norm == "blastn":
        _annotate_genes(resolved_db, hits)
    if truncated:
        logger.warning(
            "%s(alignment) output truncated: db=%s max_total_hits=%s (tune BLAST_ALIGNMENT_MAX_TOTAL_HITS if needed)",
            program_norm,
            resolved_db,
            max_total_hits,
        )

    logger.info(
        "%s(alignment) finished db=%s task=%s evalue=%s max_target_seqs=%s max_hsps=%s threads=%s hits=%s elapsed=%.3fs",
        program_norm,
        resolved_db,
        effective_task,
        evalue,
        max_target_seqs,
        max_hsps,
        threads,
        len(hits),
        elapsed,
    )

    return hits


def run_blastn_with_alignment_sync(
    sequence: str,
    db: str,
    task: str = "blastn",
    evalue: float = 1e-5,
    max_target_seqs: int = 25,
    num_threads: int | None = None,
    max_hsps: int | None = None,
    local_mode: str | None = None,
    engine: str = "blast",
) -> List[BlastHit]:
    """
    互換用: 旧 API（blastn 固定）。
    """
    return run_blast_with_alignment_sync(
        program="blastn",
        sequence=sequence,
        db=db,
        task=task,
        evalue=evalue,
        max_target_seqs=max_target_seqs,
        num_threads=num_threads,
        max_hsps=max_hsps,
        local_mode=local_mode,
        engine=engine,
    )


def run_blastn_batch_local(
    sequences: list[str],
    db: str,
    task: str = "blastn",
    evalue: float = 1e-5,
    max_target_seqs: int = 25,
    num_threads: int | None = None,
    max_hsps: int | None = None,
    local_mode: str | None = None,
    engine: str = "blast",
) -> list[list[BlastHit]]:
    """
    複数クエリ配列をまとめて 1 回の blastn で実行し、クエリごとの BlastHit リストを返す。

    - sequences[i] には任意の DNA 配列（空白混在可）を渡す。
    - 返り値は len(sequences) 要素のリストで、各要素がそのクエリに対応する BlastHit 一覧。
    """
    logger = logging.getLogger(__name__)
    engine_norm = (engine or "blast").strip().lower()
    if engine_norm not in {"blast", "cuda"}:
        engine_norm = "blast"
    meta = _meta_default(engine_norm)

    resolved_db, _label, resolved_mode = resolve_local_db_plan(db, local_mode)
    db_prefix = Path(resolved_db)

    if not _db_exists(db_prefix):
        raise BlastExecutionError(f"BLAST データベースが見つかりません: {resolved_db}")

    clean_sequences = ["".join(s.split()).upper() for s in sequences]
    if any(not s for s in clean_sequences):
        raise BlastExecutionError("空のクエリ配列が含まれています。")
    effective_task = _effective_blastn_task_for_sequences(task, clean_sequences)

    if engine_norm == "cuda":
        quarantine_reason = _cuda_quarantine_reason_for_workload(resolved_db, effective_task, clean_sequences)
        if quarantine_reason:
            logger.info(
                "cuda skipped by quarantine (batch): db=%s task=%s queries=%s reason=%s",
                resolved_db,
                effective_task,
                len(clean_sequences),
                quarantine_reason,
            )
            cpu_fallback = run_blastn_batch_local(
                sequences=sequences,
                db=db,
                task=task,
                evalue=evalue,
                max_target_seqs=max_target_seqs,
                num_threads=num_threads,
                max_hsps=max_hsps,
                local_mode="cpu",
                engine="blast",
            )
            meta.engine_executed = "blast"
            meta.fallback_used = True
            meta.fallback_reason = f"cuda quarantined: {quarantine_reason}"
            return BlastBatchHitList([list(row) for row in cpu_fallback], meta=meta)

        skip_reason = _cuda_skip_reason_for_workload(effective_task, clean_sequences)
        if skip_reason:
            logger.info(
                "cuda skipped by workload heuristic (batch): db=%s task=%s queries=%s reason=%s",
                resolved_db,
                effective_task,
                len(clean_sequences),
                skip_reason,
            )
            cpu_fallback = run_blastn_batch_local(
                sequences=sequences,
                db=db,
                task=task,
                evalue=evalue,
                max_target_seqs=max_target_seqs,
                num_threads=num_threads,
                max_hsps=max_hsps,
                local_mode="cpu",
                engine="blast",
            )
            meta.engine_executed = "blast"
            meta.fallback_used = True
            meta.fallback_reason = f"cuda skipped: {skip_reason}"
            return BlastBatchHitList([list(row) for row in cpu_fallback], meta=meta)

        if not CUDA_AVAILABLE:
            if _cuda_strict_mode():
                raise BlastExecutionError(
                    f"CUDA-BLASTN は利用不可です: {CUDA_UNAVAILABLE_REASON or 'binary unavailable'}"
                )
            cpu_fallback = run_blastn_batch_local(
                sequences=sequences,
                db=db,
                task=task,
                evalue=evalue,
                max_target_seqs=max_target_seqs,
                num_threads=num_threads,
                max_hsps=max_hsps,
                local_mode="cpu",
                engine="blast",
            )
            meta.engine_executed = "blast"
            meta.fallback_used = True
            meta.fallback_reason = f"cuda unavailable: {CUDA_UNAVAILABLE_REASON or 'binary unavailable'}"
            return BlastBatchHitList([list(row) for row in cpu_fallback], meta=meta)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=False) as tmp:
            for idx, seq in enumerate(clean_sequences):
                tmp.write(f">q{idx}\n")
                tmp.write(seq + "\n")
            tmp_path = tmp.name
        try:
            try:
                hits_flat = _run_cuda_blastn(
                    Path(tmp_path),
                    resolved_db,
                    evalue,
                    max_target_seqs,
                    task=effective_task,
                    max_hsps=max_hsps,
                    query_sequences=clean_sequences,
                )
                max_total_hits = _blast_batch_max_total_hits()
                if len(hits_flat) > max_total_hits:
                    logger.warning(
                        "cuda blastn batch output truncated: db=%s hits=%s limit=%s",
                        resolved_db,
                        len(hits_flat),
                        max_total_hits,
                    )
                    hits_flat = hits_flat[:max_total_hits]
                hits_flat.sort(key=lambda h: (-h.bitscore, -h.pident))
                _annotate_local_hits(resolved_db, hits_flat)
                _annotate_genes(resolved_db, hits_flat)
            except BlastExecutionError as exc:
                if _cuda_strict_mode():
                    raise
                _remember_cuda_quarantine(
                    resolved_db,
                    effective_task,
                    clean_sequences,
                    f"cuda execution failed: {exc}",
                )
                logger.warning(
                    "cuda-blast batch failed; fallback to BLAST+ (db=%s): %s",
                    resolved_db,
                    exc,
                )
                cpu_fallback = run_blastn_batch_local(
                    sequences=sequences,
                    db=db,
                    task=task,
                    evalue=evalue,
                    max_target_seqs=max_target_seqs,
                    num_threads=num_threads,
                    max_hsps=max_hsps,
                    local_mode="cpu",
                    engine="blast",
                )
                meta.engine_executed = "blast"
                meta.fallback_used = True
                meta.fallback_reason = f"cuda execution failed: {exc}"
                return BlastBatchHitList([list(row) for row in cpu_fallback], meta=meta)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        hits_by_query: dict[str, list[BlastHit]] = {f"q{i}": [] for i in range(len(clean_sequences))}
        for h in hits_flat:
            bucket = hits_by_query.get(h.qseqid)
            if bucket is not None:
                bucket.append(h)

        gpu_result: list[list[BlastHit]] = []
        for i in range(len(clean_sequences)):
            per_query = hits_by_query.get(f"q{i}") or []
            per_query.sort(key=lambda h: (-h.bitscore, -h.pident))
            gpu_result.append(per_query)

        gate_enabled = _blast_cuda_gate_enabled()
        need_shadow = (not gate_enabled) and (_blast_cuda_shadow_rate() > 0.0)
        if gate_enabled or need_shadow:
            sample_indices = _select_cuda_gate_sample_indices(len(clean_sequences))
            if sample_indices:
                sampled_sequences = [sequences[i] for i in sample_indices]
                gpu_gate_result = [gpu_result[i] for i in sample_indices]
            else:
                sampled_sequences = []
                gpu_gate_result = []

            cpu_reference = run_blastn_batch_local(
                sequences=sampled_sequences,
                db=db,
                task=task,
                evalue=evalue,
                max_target_seqs=max_target_seqs,
                num_threads=num_threads,
                max_hsps=max_hsps,
                local_mode="cpu",
                engine="blast",
            )
            cpu_gate_result = [list(row) for row in cpu_reference]
            gate = _evaluate_cuda_equivalence(gpu_gate_result, cpu_gate_result)
            if sample_indices and len(sample_indices) < len(clean_sequences):
                sampled_note = f"sampled {len(sample_indices)}/{len(clean_sequences)} queries"
                gate.note = f"{sampled_note} / {gate.note}" if gate.note else sampled_note
            if not gate_enabled:
                gate.enabled = False
                gate.note = f"shadow_compare: {gate.note or ''}".strip()
            meta.equivalence_gate = gate
            if gate_enabled and not gate.passed:
                logger.warning(
                    "cuda equivalence gate failed (batch): %s; fallback to CPU",
                    gate.note or "threshold not met",
                )
                _remember_cuda_quarantine(
                    resolved_db,
                    effective_task,
                    clean_sequences,
                    f"equivalence gate failed: {gate.note or 'threshold not met'}",
                )
                if len(sample_indices) == len(clean_sequences):
                    cpu_result = cpu_gate_result
                else:
                    cpu_reference_full = run_blastn_batch_local(
                        sequences=sequences,
                        db=db,
                        task=task,
                        evalue=evalue,
                        max_target_seqs=max_target_seqs,
                        num_threads=num_threads,
                        max_hsps=max_hsps,
                        local_mode="cpu",
                        engine="blast",
                    )
                    cpu_result = [list(row) for row in cpu_reference_full]
                meta.engine_executed = "blast"
                meta.fallback_used = True
                meta.fallback_reason = f"equivalence gate failed: {gate.note or 'threshold not met'}"
                return BlastBatchHitList(cpu_result, meta=meta)
            if not gate_enabled:
                logger.info(
                    "cuda shadow compare (batch): top1=%.3f top5=%.3f bitscore_delta=%s bitscore_delta_ratio=%s passed=%s",
                    gate.top1_match_rate or 0.0,
                    gate.top5_overlap_rate or 0.0,
                    f"{gate.bitscore_median_delta:.3f}" if gate.bitscore_median_delta is not None else "n/a",
                    f"{(gate.bitscore_median_delta_ratio or 0.0) * 100.0:.3f}%"
                    if gate.bitscore_median_delta_ratio is not None
                    else "n/a",
                    gate.passed,
                )

        logger.info(
            "cuda_blastn batch finished db=%s task=%s evalue=%s max_target_seqs=%s queries=%s total_hits=%s",
            resolved_db,
            effective_task,
            evalue,
            max_target_seqs,
            len(clean_sequences),
            len(hits_flat),
        )
        meta.engine_executed = "cuda"
        return BlastBatchHitList(gpu_result, meta=meta)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=True) as tmp_fa:
        for idx, seq in enumerate(clean_sequences):
            tmp_fa.write(f">q{idx}\n")
            tmp_fa.write(seq + "\n")
        tmp_fa.flush()

        threads = clamp_blast_threads(num_threads, parallel_jobs=1)
        start_time = time.perf_counter()
        task_norm = (effective_task or "").lower()
        seqidlist_path: Path | None = None
        gblastn_path = Path(GBLASTN_BLASTN) if GBLASTN_BLASTN else None
        use_gblastn = resolved_mode == "gpu" and gblastn_path is not None and gblastn_path.exists()
        if resolved_mode == "gpu" and not use_gblastn:
            prefilter = _maybe_run_gpu_prefilter(
                Path(tmp_fa.name),
                db_prefix,
                clean_sequences,
                effective_task,
            )
            if prefilter is not None:
                seqidlist_path, _seqid_count = prefilter

        cmd = [
            str(gblastn_path) if use_gblastn and gblastn_path is not None else _blast_bin("blastn"),
            "-task",
            effective_task,
            "-query",
            tmp_fa.name,
            "-db",
            resolved_db,
            "-outfmt",
            "6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore",
            "-evalue",
            str(evalue),
            "-max_target_seqs",
            str(max_target_seqs),
            "-num_threads",
            str(threads),
        ]
        if max_hsps:
            cmd.extend(["-max_hsps", str(max_hsps)])
        if task_norm == "megablast" and _has_megablast_index(db_prefix):
            max_bp = _parse_env_int("BLAST_USE_INDEX_MAX_QUERY_BP", 5000)
            if max_bp > 0:
                longest = max((len(s) for s in clean_sequences), default=0)
                if longest and longest <= max_bp:
                    cmd.extend(["-use_index", "true"])
        if resolved_mode == "gpu":
            dbsize = _get_db_total_bases(str(db_prefix))
            if dbsize:
                cmd.extend(["-dbsize", str(dbsize)])
            if seqidlist_path is not None:
                cmd.extend(["-seqidlist", str(seqidlist_path)])
        if use_gblastn:
            cmd.extend(["-use_gpu", "true"])

        timeout_sec = _blast_timeout_sec()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise BlastExecutionError(
                f"blastn(batch) がタイムアウトしました（{timeout_sec}s）。"
                "シーケンス数/DB数/ヒット数を減らすか、BLAST_SUBPROCESS_TIMEOUT_SEC を増やしてください。"
            ) from exc

        elapsed = time.perf_counter() - start_time
        if proc.returncode != 0:
            _raise_process_error(cmd, proc.returncode, proc.stderr or "")

    hits_flat: list[BlastHit] = []
    hits_by_idx: dict[int, list[BlastHit]] = {i: [] for i in range(len(clean_sequences))}
    max_total_hits = _blast_batch_max_total_hits()
    truncated = False

    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 12:
            continue
        qid = parts[0]
        try:
            idx = int(qid.removeprefix("q"))
        except ValueError:
            continue
        if idx not in hits_by_idx:
            continue
        if len(hits_flat) >= max_total_hits:
            truncated = True
            break
        hit = BlastHit(
            qseqid=parts[0],
            sseqid=parts[1],
            pident=float(parts[2]),
            length=int(parts[3]),
            mismatch=int(parts[4]),
            gapopen=int(parts[5]),
            qstart=int(parts[6]),
            qend=int(parts[7]),
            sstart=int(parts[8]),
            send=int(parts[9]),
            evalue=float(parts[10]),
            bitscore=float(parts[11]),
        )
        hits_flat.append(hit)
        hits_by_idx[idx].append(hit)

    hits_flat.sort(key=lambda h: (-h.bitscore, -h.pident))
    _annotate_local_hits(resolved_db, hits_flat)
    _annotate_genes(resolved_db, hits_flat)
    if truncated:
        logger.warning(
            "blastn batch output truncated: db=%s max_total_hits=%s (tune BLAST_BATCH_MAX_TOTAL_HITS if needed)",
            resolved_db,
            max_total_hits,
        )

    result: list[list[BlastHit]] = []
    for i in range(len(clean_sequences)):
        per_query = hits_by_idx.get(i) or []
        per_query.sort(key=lambda h: (-h.bitscore, -h.pident))
        result.append(per_query)

    logger.info(
        "blastn batch finished db=%s task=%s evalue=%s max_target_seqs=%s max_hsps=%s threads=%s queries=%s total_hits=%s elapsed=%.3fs",
        resolved_db,
        effective_task,
        evalue,
        max_target_seqs,
        max_hsps,
        threads,
        len(clean_sequences),
        len(hits_flat),
        elapsed,
    )

    meta.engine_executed = "blast"
    return BlastBatchHitList(result, meta=meta)


def fetch_sequence_local_db(
    db: str,
    entry: str,
    start: int | None = None,
    end: int | None = None,
    strand: str = "plus",
    max_len: int | None = None,
) -> str:
    """
    blastdbcmd を使って、ローカル BLAST DB から配列（または部分配列）を取得する。

    - start/end は 1-based inclusive
    - 戻り値は改行なしの大文字配列
    """
    db = normalize_db_prefix(db)
    db_prefix = Path(db)

    def _any_exists(paths: Iterable[Path]) -> bool:
        return any(p.exists() for p in paths)

    candidate_files = [
        db_prefix.with_suffix(suffix)
        for suffix in (".nhr", ".nin", ".nsq", ".ndb", ".not", ".ntf", ".nto")
    ]
    if not _any_exists(candidate_files):
        raise BlastNotFoundError(f"BLAST データベースが見つかりません: {db}")

    entry = (entry or "").strip()
    if not entry:
        raise BlastInputError("entry が空です。")
    entry = resolve_entry_for_local_db(db, entry)

    strand_norm = (strand or "plus").strip().lower()
    if strand_norm not in {"plus", "minus"}:
        raise BlastInputError('strand は "plus" / "minus" のいずれかを指定してください。')

    if max_len is None:
        max_len = _parse_env_int("BLAST_FETCH_SEQUENCE_MAX_LEN", 0)
    if max_len is not None and max_len < 0:
        max_len = 0

    range_arg: str | None = None
    if start is not None and end is not None:
        if end < start:
            raise BlastInputError("end は start 以上を指定してください。")
        if max_len and (end - start + 1) > max_len:
            raise BlastInputError(
                f"要求された範囲が大きすぎます（>{max_len}bp）。"
                " BLAST_FETCH_SEQUENCE_MAX_LEN を調整してください。"
            )
        range_arg = f"{start}-{end}"
    elif start is not None:
        range_arg = f"{start}-"
    elif end is not None:
        raise BlastInputError("end のみ指定はできません（start を指定してください）。")

    cmd = [
        _blast_bin("blastdbcmd"),
        "-db",
        db,
        "-entry",
        entry,
        "-outfmt",
        "%s",
        # BLAST+ 2.12+ では line_length=0 がエラーになるため、大きめの値を指定する。
        # （出力は改行除去して返すので、ここは 1 以上なら問題ない）
        "-line_length",
        "1000000",
        "-long_seqids",
        "-strand",
        strand_norm,
    ]
    if range_arg:
        cmd.extend(["-range", range_arg])

    timeout_sec = _blast_fetch_sequence_timeout_sec()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        # DB の accession 情報や entry 解決で遅延する環境向けに、FASTA 直接参照へフォールバックする。
        fasta_path = _find_fasta_for_db(db_prefix)
        if fasta_path is not None and start is not None and end is not None:
            seq = _fetch_sequence_from_fasta(
                fasta_path=fasta_path,
                entry=entry,
                start=start,
                end=end,
            )
            if strand_norm == "minus":
                seq = _rev_comp(seq)
            return seq
        raise BlastExecutionError(
            f"blastdbcmd がタイムアウトしました（{timeout_sec}s）。"
            "BLAST_FETCH_SEQUENCE_TIMEOUT_SEC を増やすか 0 で無制限にしてください。"
        ) from exc
    if proc.returncode == 0:
        seq = "".join(proc.stdout.split()).upper()
        if seq:
            return seq

    # makeblastdb を -parse_seqids なしで作った DB では、blastdbcmd が entry を解決できず
    # "DB contains no accession info" で落ちることがあるため FASTA 直接参照にフォールバックする。
    stderr = proc.stderr or ""
    if "DB contains no accession info" in stderr:
        fasta_path = _find_fasta_for_db(db_prefix)
        if fasta_path is None:
            raise BlastNotFoundError(
                "blastdbcmd での配列取得に失敗し、対応する FASTA も見つかりませんでした。\n"
                f"db={db}\nentry={entry}\nstderr:\n{stderr}"
            )
        seq = _fetch_sequence_from_fasta(
            fasta_path=fasta_path,
            entry=entry,
            start=start,
            end=end,
        )
        if strand_norm == "minus":
            seq = _rev_comp(seq)
        return seq

    if proc.returncode != 0:
        err_low = stderr.lower()
        if (
            "entry not found" in err_low
            or "oid not found" in err_low
            or "no entries found" in err_low
            or "entry not found in index" in err_low
        ):
            raise BlastNotFoundError(
                "指定した entry が BLAST DB に見つかりませんでした。\n"
                f"db={db}\nentry={entry}\nstderr:\n{stderr}"
            )
        raise BlastExecutionError(
            "blastdbcmd の実行に失敗しました（returncode=%s）。\nstderr:\n%s"
            % (proc.returncode, stderr)
        )
    raise BlastNotFoundError("配列を取得できませんでした（entry/range を確認してください）。")


@dataclass(frozen=True)
class _FastaLayout:
    offset: int  # 先頭塩基（1bp目）のファイルオフセット（bytes）
    line_bases: int  # 1 行あたりの塩基数（改行除く）
    line_width: int  # 1 行あたりのバイト数（改行含む）


def _find_fasta_for_db(db_prefix: Path) -> Path | None:
    direct = [
        db_prefix.with_suffix(".fa"),
        db_prefix.with_suffix(".fasta"),
        db_prefix.with_suffix(".fna"),
        db_prefix.with_suffix(".fas"),
    ]
    hit = next((p for p in direct if p.exists()), None)
    if hit:
        return hit

    parent = db_prefix.parent
    if not parent.exists():
        return None

    # DB prefix と元FASTA名が一致しないケース（例: DB=testdb, FASTA=test.fna）向け。
    # 同じディレクトリに FASTA が 1 つだけならそれを使う。
    globbed: list[Path] = []
    for pat in ("*.fa", "*.fasta", "*.fna", "*.fas"):
        globbed.extend(parent.glob(pat))
    uniq = sorted({p.resolve() for p in globbed if p.is_file()})
    if len(uniq) == 1:
        return uniq[0]
    return None


@lru_cache(maxsize=256)
def _locate_fasta_layout(fasta_path_str: str, entry: str) -> _FastaLayout:
    fasta_path = Path(fasta_path_str)
    entry_raw = (entry or "").strip()
    entry_lower = entry_raw.lower()
    entry_norm = _normalize_chr_alias(entry_raw)
    entry_norm_lower = entry_norm.lower()
    with fasta_path.open("rb") as fh:
        while True:
            line = fh.readline()
            if not line:
                break
            if not line.startswith(b">"):
                continue
            header = line[1:].strip()
            if not header:
                continue
            title = header.decode("utf-8", errors="replace")
            seqid = title.split(None, 1)[0]
            if not seqid:
                continue

            # 完全一致（最優先）
            if seqid != entry_raw and seqid.lower() != entry_lower:
                # ユーザー入力が chr1 のような場合は、FASTA の header/seqid から染色体名を推定して解決する
                chrom = _infer_chrom_from_seqid_and_title(seqid, title)
                if not chrom or chrom.lower() != entry_norm_lower:
                    continue

            # 最初の塩基行を探す（空行が混ざっていても耐える）
            while True:
                pos = fh.tell()
                seq_line = fh.readline()
                if not seq_line:
                    raise BlastNotFoundError(f"FASTA の entry が空です: {entry}")
                if seq_line.startswith(b">"):
                    raise BlastNotFoundError(f"FASTA の entry が空です: {entry}")
                stripped = seq_line.rstrip(b"\r\n")
                if not stripped:
                    continue
                line_bases = len(stripped)
                line_width = len(seq_line)
                if line_bases <= 0 or line_width <= 0:
                    raise BlastExecutionError(f"FASTA の行長を取得できませんでした: {entry}")
                return _FastaLayout(offset=pos, line_bases=line_bases, line_width=line_width)

    raise BlastNotFoundError(f"FASTA に entry が見つかりませんでした: {entry}")


def _fetch_sequence_from_fasta(
    *,
    fasta_path: Path,
    entry: str,
    start: int | None,
    end: int | None,
) -> str:
    if start is None or end is None:
        raise BlastInputError(
            "この BLAST DB は accession 情報が無いため、FASTA 直接参照で配列を取得します。"
            "そのため start/end の両方を指定してください。"
        )
    if start < 1 or end < 1:
        raise BlastInputError("start/end は 1 以上を指定してください。")
    if end < start:
        raise BlastInputError("end は start 以上を指定してください。")

    layout = _locate_fasta_layout(str(fasta_path), entry)
    s0 = start - 1
    e0 = end - 1
    lb = max(1, layout.line_bases)
    lw = max(1, layout.line_width)

    start_off = layout.offset + (s0 // lb) * lw + (s0 % lb)
    end_off = layout.offset + (e0 // lb) * lw + (e0 % lb)
    to_read = end_off - start_off + 1
    if to_read <= 0:
        return ""

    with fasta_path.open("rb") as fh:
        fh.seek(start_off)
        buf = fh.read(to_read)

    seq = buf.replace(b"\n", b"").replace(b"\r", b"").decode("utf-8", errors="replace").upper()
    if ">" in seq:
        raise BlastInputError("FASTA からの取得に失敗しました（end が配列長を超えている可能性があります）。")
    expected = end - start + 1
    if len(seq) != expected:
        raise BlastInputError(
            "FASTA からの部分配列取得に失敗しました（行長が一定でない/範囲が不正の可能性）。\n"
            f"entry={entry} start={start} end={end} expected={expected} got={len(seq)}"
        )
    return seq


def _rev_comp(seq: str) -> str:
    s = "".join(seq.split()).upper()
    trans = str.maketrans(
        {
            "A": "T",
            "T": "A",
            "C": "G",
            "G": "C",
            "R": "Y",
            "Y": "R",
            "S": "S",
            "W": "W",
            "K": "M",
            "M": "K",
            "B": "V",
            "V": "B",
            "D": "H",
            "H": "D",
            "N": "N",
        }
    )
    return s.translate(trans)[::-1]


def _resolve_gblastn_binary() -> str | None:
    """
    GPU 拡張 blastn（G-BLASTN 互換）を解決する。

    優先順位:
    1. 環境変数 GBLASTN_BIN
    2. 既知のローカル候補パス
    3. PATH 上の gblastn / blastn_gpu
    """
    logger = logging.getLogger(__name__)
    env_bin = (os.getenv("GBLASTN_BIN") or "").strip()
    if env_bin:
        p = Path(expand_user_path(env_bin))
        if p.exists() and p.is_file() and os.access(str(p), os.X_OK):
            ok, reason = _probe_binary_runtime(str(p), probe_args=["-version"])
            if ok:
                return str(p)
            logger.warning("GBLASTN_BIN is not runnable: %s (%s)", env_bin, reason or "unknown")
        else:
            logger.warning("GBLASTN_BIN is set but not found: %s", env_bin)

    home = Path.home()
    preferred_candidates = [
        home / "sequence_workbench/tools/G-BLASTN/c++/GCC1300-ReleaseMT64/bin/blastn",
        home / "G-BLASTN/c++/GCC1300-ReleaseMT64/bin/blastn",
        Path("/mnt/c/Users/user.Windows-PC/projects/G-BLASTN/c++/GCC1300-ReleaseMT64/bin/blastn"),
    ]
    for p in preferred_candidates:
        if p.exists() and p.is_file() and os.access(str(p), os.X_OK):
            ok, reason = _probe_binary_runtime(str(p), probe_args=["-version"])
            if ok:
                return str(p)
            logger.warning("Skipping unrunnable G-BLASTN candidate: %s (%s)", p, reason or "unknown")

    for name in ("gblastn", "blastn_gpu"):
        hit = shutil.which(name)
        if hit:
            ok, reason = _probe_binary_runtime(hit, probe_args=["-version"])
            if ok:
                return hit
            logger.warning("Skipping unrunnable G-BLASTN binary on PATH: %s (%s)", hit, reason or "unknown")
    return None


def _infer_cuda_blastn_flavor(binary_path: str) -> str:
    name = Path(binary_path).name.lower()
    if "cuda_blastn_opt" in name:
        return "legacy_opt"
    if "cuda-blastn" in name or "cuda_blastn" in name:
        return "cuda_blastn_cli"
    return "unknown"


def _resolve_cuda_blastn_binary() -> tuple[str | None, str]:
    """
    CUDA BLAST 実行バイナリを解決する。

    優先順位:
    1. 環境変数 CUDA_BLASTN
    2. この環境で利用実績のある候補パス
    3. PATH 上の cuda-blastn / cuda_blastn_opt
    """
    logger = logging.getLogger(__name__)

    env_bin = (os.getenv("CUDA_BLASTN") or "").strip()
    if env_bin:
        p = Path(expand_user_path(env_bin))
        if p.exists() and p.is_file() and os.access(str(p), os.X_OK):
            ok, reason = _probe_binary_runtime(str(p), probe_args=["--version"])
            if ok:
                return str(p), _infer_cuda_blastn_flavor(str(p))
            logger.warning("CUDA_BLASTN is configured but not runnable: %s (%s)", env_bin, reason or "unknown")
        else:
            logger.warning("CUDA_BLASTN is set but not found: %s", env_bin)

    home = Path.home()
    preferred_candidates = [
        home / "cuda-blastn/bin/cuda-blastn",
        home / "cuda-blastn/build/cuda_blastn_opt",
        Path("/mnt/c/Users/user.Windows-PC/projects/cuda-blastn/build/cuda_blastn_opt"),
    ]
    for p in preferred_candidates:
        if p.exists() and p.is_file() and os.access(str(p), os.X_OK):
            ok, reason = _probe_binary_runtime(str(p), probe_args=["--version"])
            if ok:
                return str(p), _infer_cuda_blastn_flavor(str(p))
            logger.warning("Skipping unrunnable CUDA BLAST binary: %s (%s)", p, reason or "unknown")

    for name in ("cuda-blastn", "cuda_blastn_opt"):
        hit = shutil.which(name)
        if hit:
            ok, reason = _probe_binary_runtime(hit, probe_args=["--version"])
            if ok:
                return hit, _infer_cuda_blastn_flavor(hit)
            logger.warning("Skipping unrunnable CUDA BLAST binary on PATH: %s (%s)", hit, reason or "unknown")

    return None, "unavailable"


def _resolve_cuda_db_fasta(db_prefix: str) -> str:
    """
    CUDA 系バイナリに渡す DB FASTA パスを解決する。

    フロント側は BLAST DB prefix（拡張子なし）を渡すため、
    `.fa/.fasta/.fna/.fas/.pep.fa` を順に探して使う。
    """
    db_norm = normalize_db_prefix(db_prefix)
    p = Path(db_norm)
    candidates = [
        p,
        Path(f"{db_norm}.fa"),
        Path(f"{db_norm}.fasta"),
        Path(f"{db_norm}.fna"),
        Path(f"{db_norm}.fas"),
        Path(f"{db_norm}.pep.fa"),
    ]
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if cand.exists() and cand.is_file():
            return str(cand)
    raise BlastExecutionError(
        "CUDA-BLAST 用の FASTA が見つかりません。"
        f" db={db_prefix}（{db_norm}.fa / .fasta / .fna などを確認してください）"
    )


CUDA_BLASTN, CUDA_BLASTN_FLAVOR = _resolve_cuda_blastn_binary()
CUDA_AVAILABLE = bool(CUDA_BLASTN)
CUDA_UNAVAILABLE_REASON: str | None = None
if CUDA_AVAILABLE:
    logging.getLogger(__name__).info(
        "CUDA BLAST backend enabled: %s (flavor=%s)",
        CUDA_BLASTN,
        CUDA_BLASTN_FLAVOR,
    )
else:
    logging.getLogger(__name__).warning(
        "CUDA BLAST backend unavailable: no CUDA_BLASTN binary found",
    )

GBLASTN_BLASTN = _resolve_gblastn_binary()
GBLASTN_AVAILABLE = bool(GBLASTN_BLASTN)
if GBLASTN_AVAILABLE:
    logging.getLogger(__name__).info("G-BLASTN backend enabled: %s", GBLASTN_BLASTN)
else:
    logging.getLogger(__name__).info("G-BLASTN backend unavailable")

NCBI_BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"
ENSEMBL_BLAST_BASE = "https://rest.ensembl.org"


def run_blast_ncbi(
    sequence: str,
    program: str = "blastn",
    database: str = "nt",
    evalue: float = 1e-5,
    max_target_seqs: int = 25,
    entrez_query: str | None = "",
    poll_interval: float = 3.0,
    max_poll: int = 10,
) -> List[BlastHit]:
    """
    NCBI の BLAST URL API を利用してリモート BLAST を実行する。

    - クエリは nt データベースに対して blastn で投げる。
    - レスポンスは XML として取得し、上位の HSP 情報のみをパースして返す。
    """
    seq = "".join(sequence.split()).upper()
    if not seq:
        raise BlastExecutionError("クエリ配列が空です。")

    settings = get_settings()
    api_key = getattr(settings, "ncbi_api_key", None)

    # 1. ジョブ投入（CMD=Put）
    put_params = {
        "CMD": "Put",
        "PROGRAM": program,
        "DATABASE": database,
        "HITLIST_SIZE": str(max_target_seqs),
        "EXPECT": str(evalue),
        "FORMAT_TYPE": "XML",
    }
    if entrez_query:
        put_params["ENTREZ_QUERY"] = entrez_query
    if api_key:
        put_params["NCBI_API_KEY"] = api_key

    with httpx.Client(timeout=60.0) as client:
        put_resp = client.post(
            NCBI_BLAST_URL,
            data={"QUERY": seq},
            params=put_params,
        )

    if put_resp.status_code != 200:
        raise BlastExecutionError(
            f"NCBI BLAST への PUT に失敗しました（status={put_resp.status_code}）。"
        )

    match = None
    for line in put_resp.text.splitlines():
        if line.strip().startswith("RID ="):
            _, rid_val = line.split("=", 1)
            match = rid_val.strip()
            break
    if not match:
        raise BlastExecutionError("NCBI BLAST 応答から RID を取得できませんでした。")

    rid = match

    # 2. ジョブ完了までポーリング（CMD=Get）
    xml_text: str | None = None
    for _ in range(max_poll):
        time.sleep(poll_interval)
        get_params = {
            "CMD": "Get",
            "RID": rid,
            "FORMAT_TYPE": "XML",
        }
        if api_key:
            get_params["NCBI_API_KEY"] = api_key

        with httpx.Client(timeout=60.0) as client:
            get_resp = client.get(NCBI_BLAST_URL, params=get_params)

        if get_resp.status_code != 200:
            continue

        text = get_resp.text
        if "Status=WAITING" in text:
            continue
        if "Status=FAILED" in text or "Status=UNKNOWN" in text:
            raise BlastExecutionError("NCBI BLAST ジョブが失敗または不明な状態になりました。")

        xml_text = text
        break

    if xml_text is None:
        raise BlastExecutionError("NCBI BLAST の結果取得がタイムアウトしました。")

    # 3. XML をパースして BlastHit のリストに変換
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise BlastExecutionError(f"NCBI BLAST の XML を解析できませんでした: {exc}") from exc

    hits: List[BlastHit] = []

    for hit_el in root.findall(".//Hit"):
        hit_id = (hit_el.findtext("Hit_id") or "").strip()
        hit_def = (hit_el.findtext("Hit_def") or "").strip()
        sseqid = (hit_id + " " + hit_def).strip()

        hsp_el = hit_el.find(".//Hsp")
        if hsp_el is None:
            continue

        try:
            length = int(hsp_el.findtext("Hsp_align-len") or "0")
            identity = int(hsp_el.findtext("Hsp_identity") or "0")
            e_val = float(hsp_el.findtext("Hsp_evalue") or "0")
            bit_score = float(hsp_el.findtext("Hsp_bit-score") or "0")
            qstart = int(hsp_el.findtext("Hsp_query-from") or "0")
            qend = int(hsp_el.findtext("Hsp_query-to") or "0")
            sstart = int(hsp_el.findtext("Hsp_hit-from") or "0")
            send = int(hsp_el.findtext("Hsp_hit-to") or "0")
        except ValueError:
            continue

        pident = (100.0 * identity / length) if length > 0 else 0.0

        hits.append(
            BlastHit(
                qseqid="query",
                sseqid=sseqid,
                pident=pident,
                length=length,
                mismatch=length - identity,
                gapopen=0,
                qstart=qstart,
                qend=qend,
                sstart=sstart,
                send=send,
                evalue=e_val,
                bitscore=bit_score,
            )
        )

    # 一致度の高い順（bitscore, pident）にソート
    hits.sort(key=lambda h: (-h.bitscore, -h.pident))

    return hits


def run_blast_ensembl(
    sequence: str,
    species: str = "homo_sapiens",
    db_type: str = "core",
    evalue: float = 1e-5,
    max_target_seqs: int = 25,
    poll_interval: float = 3.0,
    max_poll: int = 10,
) -> List[BlastHit]:
    """
    Ensembl REST の BLAST API を利用してリモート BLAST を実行する。

    注意: 実際の API スキーマは Ensembl REST のドキュメントに従って調整が必要な場合がある。
    """
    seq = "".join(sequence.split()).upper()
    if not seq:
        raise BlastExecutionError("クエリ配列が空です。")

    # 1. ジョブ投入
    post_url = f"{ENSEMBL_BLAST_BASE}/blast/{species}"
    payload = {
        "sequence": seq,
        "db_type": db_type,
        "hit_count": max_target_seqs,
        "threshold": evalue,
    }

    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    with httpx.Client(timeout=60.0) as client:
        put_resp = client.post(post_url, json=payload, headers=headers)

    if put_resp.status_code not in (200, 202):
        raise BlastExecutionError(
            f"Ensembl BLAST への POST に失敗しました（status={put_resp.status_code}）。"
        )

    data = put_resp.json()
    job_id = data.get("job_id") or data.get("id")
    if not job_id:
        raise BlastExecutionError("Ensembl BLAST 応答から job_id を取得できませんでした。")

    # 2. ジョブ完了までポーリング
    result_url = f"{ENSEMBL_BLAST_BASE}/blast/{species}/{job_id}"
    result_json = None
    for _ in range(max_poll):
        time.sleep(poll_interval)
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(result_url, headers={"Accept": "application/json"})

        if resp.status_code == 404:
            continue
        if resp.status_code != 200:
            continue

        result_json = resp.json()
        # Ensembl のレスポンス構造に応じて、完了判定が必要ならここで行う
        break

    if result_json is None:
        raise BlastExecutionError("Ensembl BLAST の結果取得がタイムアウトしました。")

    hits: List[BlastHit] = []

    for hit in result_json.get("hits", []):
        hsp_list = hit.get("hsps") or []
        if not hsp_list:
            continue
        hsp = hsp_list[0]
        try:
            length = int(hsp.get("align_len") or 0)
            identity = int(hsp.get("identity") or 0)
            e_val = float(hsp.get("evalue") or 0)
            bit_score = float(hsp.get("score") or 0)
            qstart = int(hsp.get("qstart") or 0)
            qend = int(hsp.get("qend") or 0)
            sstart = int(hsp.get("start") or 0)
            send = int(hsp.get("end") or 0)
        except (TypeError, ValueError):
            continue

        pident = (100.0 * identity / length) if length > 0 else 0.0

        hits.append(
            BlastHit(
                qseqid="query",
                sseqid=str(hit.get("hit_id") or ""),
                pident=pident,
                length=length,
                mismatch=length - identity,
                gapopen=0,
                qstart=qstart,
                qend=qend,
                sstart=sstart,
                send=send,
                evalue=e_val,
                bitscore=bit_score,
            )
        )

    hits.sort(key=lambda h: (-h.bitscore, -h.pident))
    return hits


def _run_cuda_blastn(
    query_fasta: Path,
    db_prefix: str,
    evalue: float = 1e-5,
    max_target_seqs: int = 25,
    *,
    task: str = "blastn",
    max_hsps: int | None = None,
    query_sequences: list[str] | None = None,
) -> list[BlastHit]:
    """
    WSL 上の CUDA-BLASTN を実行し、結果をパースする。
    """
    if not CUDA_AVAILABLE or not CUDA_BLASTN:
        raise BlastExecutionError(
            "CUDA-BLASTN バイナリが見つかりません。"
            "環境変数 CUDA_BLASTN を設定するか、~/cuda-blastn/bin/cuda-blastn を配置してください。"
        )

    db_fasta = _resolve_cuda_db_fasta(db_prefix)
    flavor = CUDA_BLASTN_FLAVOR

    cuda_params = _cuda_task_params(task, max_hsps)
    cuda_params = _autotune_cuda_params_for_workload(
        task=task,
        params=cuda_params,
        sequences=query_sequences,
    )

    if flavor == "cuda_blastn_cli":
        # 新しい CLI 形式（-query / -db）
        cmd = [
            CUDA_BLASTN,
            "-query",
            str(query_fasta),
            "-db",
            db_fasta,
            "-outfmt",
            "6",
            "-evalue",
            str(evalue),
            "-max_target_seqs",
            str(max_target_seqs),
            "-task",
            str(cuda_params["task"]),
            "-word_size",
            str(cuda_params["word_size"]),
            "-reward",
            str(cuda_params["reward"]),
            "-penalty",
            str(cuda_params["penalty"]),
            "-xdrop_ungap",
            str(cuda_params["xdrop_ungap"]),
            "-gapopen",
            str(cuda_params["gapopen"]),
            "-gapextend",
            str(cuda_params["gapextend"]),
            "-stats_mode",
            str(cuda_params["stats_mode"]),
            "-dust",
            str(cuda_params["dust"]),
            "-soft_masking",
            str(cuda_params["soft_masking"]),
            "-seed_index_mode",
            str(cuda_params["seed_index_mode"]),
            "-max_segments",
            str(cuda_params["max_segments"]),
            "-max_kmer_occ",
            str(cuda_params["max_kmer_occ"]),
            "-max_seeds",
            str(cuda_params["max_seeds"]),
        ]
        if cuda_params.get("max_hsps"):
            cmd.extend(["-max_hsps", str(cuda_params["max_hsps"])])
        if cuda_params.get("equiv_debug_dump"):
            cmd.extend(["-equiv_debug_dump", str(cuda_params["equiv_debug_dump"])])
    else:
        # 旧形式（positional args）
        cmd = [
            CUDA_BLASTN,
            str(query_fasta),
            db_fasta,
        ]
        # legacy 形式では追加パラメータはサポート外（互換優先）。

    timeout_sec = _blast_cuda_timeout_sec()
    try:
        proc = _run_command_with_timeout(cmd, timeout_sec)
    except subprocess.TimeoutExpired as exc:
        raise BlastExecutionError(
            f"CUDA-BLASTN がタイムアウトしました（{timeout_sec}s）。"
            "条件を軽くするか、BLAST_CUDA_TIMEOUT_SEC / BLAST_SUBPROCESS_TIMEOUT_SEC を増やしてください。"
        ) from exc
    except OSError as exc:
        _disable_cuda_backend(str(exc))
        raise BlastExecutionError(
            "CUDA-BLASTN を起動できません。CUDA runtime の設定を確認してください。\n"
            f"reason: {exc}"
        ) from exc
    if proc.returncode != 0:
        stderr = proc.stderr or ""
        combined = "\n".join([stderr, proc.stdout or ""]).strip()
        if proc.returncode in (126, 127) and _looks_like_missing_shared_library(combined):
            _disable_cuda_backend(combined)
        elif _looks_like_fatal_cuda_runtime_error(combined):
            _disable_cuda_backend(combined)
        _raise_process_error(cmd, proc.returncode, stderr)

    # 実装により stdout/stderr の使い分けが異なるため両方を見る
    combined = "\n".join([(proc.stdout or ""), (proc.stderr or "")])
    return _parse_cuda_output(combined)

def _parse_cuda_output(output: str) -> list[BlastHit]:
    hits: list[BlastHit] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        try:
            qseqid = parts[0]
            sseqid = parts[1]
            pident = float(parts[2])
            length = int(parts[3])
            mismatch = int(parts[4])
            gapopen = int(parts[5])
            qstart = int(parts[6])
            qend = int(parts[7])
            sstart = int(parts[8])
            send = int(parts[9])
            evalue = float(parts[10])
            bitscore = float(parts[11])
            
            hits.append(BlastHit(
                qseqid=qseqid,
                sseqid=sseqid,
                pident=pident,
                length=length,
                mismatch=mismatch,
                gapopen=gapopen,
                qstart=qstart,
                qend=qend,
                sstart=sstart,
                send=send,
                evalue=evalue,
                bitscore=bitscore
            ))
        except ValueError:
            continue
    return hits




