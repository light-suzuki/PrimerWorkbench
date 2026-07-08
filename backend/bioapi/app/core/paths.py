from __future__ import annotations

import os
from pathlib import Path


def expand_user_path(raw: str) -> str:
    """
    Expand "~" and "$VARS" in a user-provided path string.

    This is intentionally best-effort and does not validate existence.
    """
    s = (raw or "").strip()
    if not s:
        return s
    return os.path.expandvars(os.path.expanduser(s))


def data_home() -> Path:
    """
    Return the base directory that contains user data (blast_databases, tmp, ...).

    Priority:
    - $SEQWB_DATA_DIR
    - ~/.sequence_workbench
    """
    raw = (os.getenv("SEQWB_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return (Path.home() / ".sequence_workbench").expanduser()


def workbench_tmp_dir() -> Path:
    return data_home() / "tmp"


def blast_databases_dir() -> Path:
    """
    Directory containing local BLAST DB prefixes.

    Override:
    - $BLAST_DATABASES_DIR
    """
    raw = (os.getenv("BLAST_DATABASES_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return data_home() / "blast_databases"


def blast_cold_databases_dir() -> Path | None:
    """
    Optional secondary directory for cold / derived BLAST assets.

    Overrides:
    - $BLAST_COLD_DATABASES_DIR
    - $BLAST_DERIVED_DATABASES_DIR
    """
    raw = (os.getenv("BLAST_COLD_DATABASES_DIR") or os.getenv("BLAST_DERIVED_DATABASES_DIR") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def dataverse_files_dir() -> Path:
    """
    Directory containing large auxiliary Dataverse assets.

    Override:
    - $DATAVERSE_FILES_DIR
    Fallback:
    - $BLAST_COLD_DATABASES_DIR/dataverse_files if it exists
    - $BLAST_DATABASES_DIR/dataverse_files
    """
    raw = (os.getenv("DATAVERSE_FILES_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    cold = blast_cold_databases_dir()
    if cold:
        cold_dir = cold / "dataverse_files"
        if cold_dir.exists():
            return cold_dir
    return blast_databases_dir() / "dataverse_files"


def blast_gpu_prefilter_cache_dir() -> Path:
    """
    Cache directory for GPU prefilter artifacts.

    Override:
    - $BLAST_GPU_PREFILTER_CACHE_DIR
    """
    raw = (os.getenv("BLAST_GPU_PREFILTER_CACHE_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return workbench_tmp_dir() / "blast_gpu_prefilter"

