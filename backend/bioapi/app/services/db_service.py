"""
ローカル BLAST DB（makeblastdb）をダウンロード・登録するユーティリティ。

- 単体利用前提のため認証はしない（UI 側でしか触れない想定）
- ただしファイル書き込み・外部URL取得を伴うため、例外・進捗・後始末は丁寧に扱う
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..core.paths import blast_databases_dir
from ..models.schemas import DbRegistryItem

logger = logging.getLogger(__name__)

_REGISTRY_LOCK = threading.Lock()


def get_registry_path() -> Path:
    return blast_databases_dir() / "registry.json"


def _load_registry_unlocked() -> list[DbRegistryItem]:
    p = get_registry_path()
    if not p.exists():
        return []
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return [DbRegistryItem(**item) for item in data]
    except Exception as exc:  # noqa: BLE001 - best effort
        logger.error("Failed to load registry: %s", exc)
        return []

def load_registry() -> list[DbRegistryItem]:
    with _REGISTRY_LOCK:
        return _load_registry_unlocked()


def _save_registry_unlocked(items: list[DbRegistryItem]) -> None:
    p = get_registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = [item.model_dump() for item in items]
    # Atomic write: write to tmp file and replace.
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(p.parent),
        prefix=f".{p.name}.",
        suffix=".tmp",
        delete=False,
    ) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
        tmp_name = f.name
    os.replace(tmp_name, p)

def save_registry(items: list[DbRegistryItem]) -> None:
    with _REGISTRY_LOCK:
        _save_registry_unlocked(items)

def add_to_registry(item: DbRegistryItem) -> None:
    with _REGISTRY_LOCK:
        items = _load_registry_unlocked()
        # Remove existing with same ID if any
        items = [x for x in items if x.id != item.id]
        items.append(item)
        _save_registry_unlocked(items)


def remove_from_registry(db_id: str) -> None:
    with _REGISTRY_LOCK:
        items = _load_registry_unlocked()
        items = [x for x in items if x.id != db_id]
        _save_registry_unlocked(items)


def get_db_path(db_id: str) -> Path:
    base = blast_databases_dir()
    result = (base / db_id).resolve()
    # Prevent path traversal: resolved path must stay within the base directory
    if not str(result).startswith(str(base.resolve())):
        raise ValueError(f"Invalid db_id (path traversal detected): {db_id}")
    return result


async def download_file(
    url: str,
    dest_path: Path,
    *,
    progress_callback: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    timeout = httpx.Timeout(120.0)
    headers = {"User-Agent": "EnsemblBioAPIWorkbench/0.1 (personal localhost)"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with dest_path.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total > 0:
                        await progress_callback(downloaded / total)


def decompress_gzip(src: Path, dest: Path) -> None:
    with gzip.open(src, "rb") as f_in:
        with dest.open("wb") as f_out:
            shutil.copyfileobj(f_in, f_out)


def _makeblastdb_timeout_sec() -> float | None:
    """Return makeblastdb subprocess timeout in seconds.

    Configurable via ``MAKEBLASTDB_TIMEOUT_SEC`` env var.
    ``0`` means *unlimited* (returns ``None``).
    Default is ``0`` (no limit) — large genome indexing can take hours.
    """
    raw = os.environ.get("MAKEBLASTDB_TIMEOUT_SEC", "0")
    try:
        val = int(raw)
    except (ValueError, TypeError):
        return None
    return None if val <= 0 else float(val)


def run_makeblastdb(fasta_path: Path, db_type: str, title: str, out_prefix: Path) -> None:
    cmd = [
        "makeblastdb",
        "-in", str(fasta_path),
        "-dbtype", db_type,
        "-title", title,
        "-out", str(out_prefix),
        "-parse_seqids"
    ]
    logger.info("Running makeblastdb: %s", " ".join(cmd))
    timeout = _makeblastdb_timeout_sec()
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"makeblastdb failed (rc={proc.returncode}): {proc.stderr.strip()}")


async def download_and_index_task(job: Any, url: str, name: str, db_id: str, db_type: str) -> dict[str, object]:
    """
    Job worker function (async).

    NOTE:
    - job は job_service.Job を想定するが、循環importを避けるため Any にしている。
    """
    work_dir = blast_databases_dir()
    work_dir.mkdir(parents=True, exist_ok=True)

    temp_download = work_dir / f"{db_id}.download.tmp"
    final_fasta = work_dir / f"{db_id}.fasta"

    try:
        # 1. Download
        job.update(progress=0.1, message="Downloading FASTA...")

        async def on_progress(p: float) -> None:
            # Map 0.1 -> 0.6
            job.update(progress=0.1 + (p * 0.5), message=f"Downloading... {int(p*100)}%")

        await download_file(url, temp_download, progress_callback=on_progress)

        # 2. Decompress if needed
        if url.endswith(".gz"):
            job.update(progress=0.6, message="Decompressing...")
            # Run in thread to avoid blocking loop
            await asyncio.to_thread(decompress_gzip, temp_download, final_fasta)
        else:
            shutil.move(str(temp_download), str(final_fasta))

        # 3. Makeblastdb
        job.update(progress=0.7, message="Building BLAST index (makeblastdb)...")
        out_prefix = work_dir / db_id
        await asyncio.to_thread(run_makeblastdb, final_fasta, db_type, name, out_prefix)

        # 4. Cleanup FASTA (Optional? existing system keeps fasta usually)
        # keeping fasta is good for "Get Sequence".

        # 5. Register
        job.update(progress=0.9, message="Registering DB...")
        item = DbRegistryItem(
            id=db_id,
            name=name,
            db_type=db_type,
            source_url=url,
            created_at=datetime.now(timezone.utc).isoformat(),
            file_path=str(out_prefix)
        )
        # Run sync registry update in thread as it does I/O
        await asyncio.to_thread(add_to_registry, item)

        job.update(progress=1.0, message="Completed")
        return {"status": "success", "db_id": db_id}

    except Exception:  # noqa: BLE001 - surfaced via job_service
        logger.exception("DB download task failed")
        raise
    finally:
        # best-effort cleanup (on success, temp file is moved or already deleted)
        try:
            temp_download.unlink(missing_ok=True)
        except Exception:
            pass


def scan_and_register_existing() -> int:
    """Scan directory and add missing DBs to registry"""
    base = blast_databases_dir()
    if not base.exists():
        return 0

    with _REGISTRY_LOCK:
        known_ids = {item.id for item in _load_registry_unlocked()}
    
    # Simple logic: check for .nsq or .psq
    new_items: list[DbRegistryItem] = []
    
    # Nucl
    for p in base.glob("*.nsq"):
        db_id = p.stem # remove .nsq
        if db_id in known_ids: continue
        new_items.append(DbRegistryItem(
            id=db_id,
            name=f"{db_id} (Auto-detected)",
            db_type="nucl",
            created_at=datetime.now().isoformat(),
            file_path=str(base / db_id)
        ))
        known_ids.add(db_id)
        
    # Prot
    for p in base.glob("*.psq"):
        db_id = p.stem 
        if db_id in known_ids: continue
        new_items.append(DbRegistryItem(
            id=db_id,
            name=f"{db_id} (Auto-detected)",
            db_type="prot",
            created_at=datetime.now().isoformat(),
            file_path=str(base / db_id)
        ))
        known_ids.add(db_id)

    if new_items:
        with _REGISTRY_LOCK:
            current = _load_registry_unlocked()
            current.extend(new_items)
            _save_registry_unlocked(current)
    
    return len(new_items)

