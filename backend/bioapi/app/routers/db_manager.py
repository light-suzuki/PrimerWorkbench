"""
ローカル BLAST DB を管理する API。

ローカル単体利用前提（認証なし）なので、パス取り扱いなどは最低限の安全策のみ入れる。
"""

import logging
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from ..models.schemas import DbRegistryItem, DbDownloadRequest, JobCreateResponse, DbIndexRequest
from ..services import db_service, job_service
from ..core.paths import blast_databases_dir

router = APIRouter(prefix="/admin/dbs", tags=["db_manager"])


logger = logging.getLogger(__name__)


def sanitize_id(name: str) -> str:
    # Replace non-alphanumeric (except underscore, hyphen, dot) with underscore, lowercase
    s = re.sub(r"[^a-zA-Z0-9_.\-]", "_", name)
    # Prevent path traversal components
    s = s.replace("..", "_")
    return s.lower()


@router.get("", response_model=list[DbRegistryItem])
async def list_dbs() -> list[DbRegistryItem]:
    """
    登録済みDB一覧を返す。

    - まずは registry.json の内容をそのまま返す（必要なら将来 existence チェック等を追加）
    """
    return db_service.load_registry()


@router.post("/download", response_model=JobCreateResponse)
async def download_db(request: DbDownloadRequest) -> JobCreateResponse:
    """
    URLからDBをダウンロード・構築するジョブを開始する。
    """
    # Validate URL scheme to prevent SSRF against internal services
    parsed_url = urlparse(request.url)
    if parsed_url.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=400,
            detail="URL は http:// または https:// で始まる必要があります。",
        )
    if not parsed_url.hostname:
        raise HTTPException(status_code=400, detail="URL のホスト名が不正です。")

    base_id = sanitize_id(request.name)
    if not base_id:
        base_id = "downloaded_db"

    # Duplicate ID: append timestamp
    if any(x.id == base_id for x in db_service.load_registry()):
        base_id = f"{base_id}_{int(time.time())}"

    db_id = base_id

    def work(job: job_service.Job):
        # NOTE: download_and_index_task is async; job_service will await it.
        return db_service.download_and_index_task(job, request.url, request.name, db_id, request.db_type)

    try:
        job = job_service.submit_job(f"download_db_{db_id}", work)
    except job_service.JobQueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JobCreateResponse(job_id=job.id)


@router.delete("/{db_id}")
async def delete_db(db_id: str):
    """
    DBを削除する（ファイルとレジストリ）。
    """
    items = db_service.load_registry()
    target = next((x for x in items if x.id == db_id), None)

    if not target:
        raise HTTPException(status_code=404, detail="DB not found in registry")

    base_dir = blast_databases_dir()

    # Security check: db_id shouldn't contain path traversal
    if not db_id or Path(db_id).name != db_id or "/" in db_id or "\\" in db_id or ".." in db_id:
        raise HTTPException(status_code=400, detail="Invalid DB ID")

    # Delete related files
    count = 0
    for p in base_dir.glob(f"{db_id}.*"):
        try:
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)
            count += 1
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            logger.warning("Failed to delete %s: %s", p, exc)

    db_service.remove_from_registry(db_id)
    return {"status": "deleted", "files_removed": count}


@router.post("/index_existing")
async def index_existing_dbs(_request: DbIndexRequest):
    """
    既存のディレクトリをスキャンして未登録DBを登録する。
    """
    count = db_service.scan_and_register_existing()
    return {"added": count}
