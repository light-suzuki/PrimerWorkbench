"""
シングルユーザー前提の軽量ジョブ実行基盤。

- 重い処理（CAPS 大量生成 / 大量 BLAST など）をバックグラウンドで実行し、
  フロントエンドは job_id をポーリングして進捗と結果を取得する。
- 永続化はしない（プロセス再起動で消える）。
"""

from __future__ import annotations

import asyncio
import inspect
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


JobStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]


class JobCancelled(RuntimeError):
    """ジョブがキャンセルされたことを表す例外。"""


class JobQueueFull(RuntimeError):
    """ジョブキューの上限超過。"""


@dataclass
class Job:
    id: str
    kind: str
    status: JobStatus = "queued"
    progress: float = 0.0
    message: str | None = None
    error: str | None = None
    result: Any = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    updated_at: float = field(default_factory=time.time)

    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _future: Future | None = field(default=None, repr=False)

    def update(self, *, progress: float | None = None, message: str | None = None) -> None:
        with self._lock:
            if progress is not None:
                self.progress = max(0.0, min(1.0, float(progress)))
            if message is not None:
                self.message = message
            self.updated_at = time.time()

    def cancel(self) -> None:
        with self._lock:
            if self.status in {"succeeded", "failed", "canceled"}:
                return
            self._cancel.set()
            self.message = "cancel requested"
            self.updated_at = time.time()

    def is_cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def raise_if_cancel_requested(self) -> None:
        if self.is_cancel_requested():
            raise JobCancelled("ジョブがキャンセルされました。")

    def to_public_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "job_id": self.id,
                "kind": self.kind,
                "status": self.status,
                "progress": float(self.progress),
                "message": self.message,
                "error": self.error,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "updated_at": self.updated_at,
            }


def _parse_env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        val = default
    else:
        try:
            val = int(raw)
        except ValueError:
            val = default
    val = max(minimum, val)
    if maximum is not None:
        val = min(val, maximum)
    return val


def _parse_kind_limits(defaults: dict[str, int]) -> dict[str, int]:
    limits: dict[str, int] = {
        str(k).strip(): max(1, int(v))
        for k, v in defaults.items()
        if str(k).strip()
    }
    raw = (os.getenv("JOB_KIND_LIMITS") or "").strip()
    if not raw:
        return limits

    for token in raw.split(","):
        item = token.strip()
        if not item:
            continue
        sep = "=" if "=" in item else ":" if ":" in item else None
        if not sep:
            continue
        kind, val_raw = item.split(sep, 1)
        kind = kind.strip()
        if not kind:
            continue
        try:
            val = int(val_raw.strip())
        except ValueError:
            continue
        # 0 以下は「制限を外す」扱い
        if val <= 0:
            limits.pop(kind, None)
            continue
        limits[kind] = val
    return limits


_DEFAULT_KIND_LIMITS = {
    # 高負荷ジョブは既定で同時実行を抑える（必要なら JOB_KIND_LIMITS で上書き）
    "blast_batch_local": 1,
    "blast_or": 1,
    "caps_design": 1,
    "build_chrom_aliases": 1,
    "local_gene_map_bootstrap": 1,
}

_EXECUTOR = ThreadPoolExecutor(
    max_workers=_parse_env_int("JOB_EXECUTOR_MAX_WORKERS", 4, minimum=1, maximum=32)
)
_KIND_LIMITS = _parse_kind_limits(_DEFAULT_KIND_LIMITS)
_KIND_SEMAPHORES: dict[str, threading.BoundedSemaphore] = {
    kind: threading.BoundedSemaphore(value=limit) for kind, limit in _KIND_LIMITS.items()
}
_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()

# 待機キュー上限（0: 無制限）
# 大きいクエリを拒否せず待機させたい場合を想定し、既定は無制限。
_MAX_QUEUED_JOBS = _parse_env_int("JOB_MAX_QUEUED", 0, minimum=0, maximum=50000)
# kind ごとの inflight 上限倍率（0: 制限しない）
_KIND_QUEUE_MULTIPLIER = _parse_env_int("JOB_KIND_QUEUE_MULTIPLIER", 0, minimum=0, maximum=5000)
# queued/running を含む in-flight レコード総数上限
_MAX_TOTAL_RECORDS = _parse_env_int("JOB_MAX_TOTAL_RECORDS", 1000, minimum=1, maximum=50000)

# メモリ膨張防止: 完了ジョブは最大この件数まで保持
_MAX_JOBS = 200

# 古い完了ジョブはこの秒数を超えると掃除対象
_JOB_TTL_SEC = 60 * 60 * 12  # 12h


def _cleanup_jobs_locked(now: float) -> None:
    finished = [
        j
        for j in _JOBS.values()
        if j.finished_at is not None and (now - j.finished_at) > _JOB_TTL_SEC
    ]
    for j in finished:
        _JOBS.pop(j.id, None)

    # 件数上限
    if len(_JOBS) <= _MAX_JOBS:
        return
    # 古い完了ジョブから落とす
    candidates = sorted(
        (j for j in _JOBS.values() if j.finished_at is not None),
        key=lambda x: x.finished_at or 0.0,
    )
    while len(_JOBS) > _MAX_JOBS and candidates:
        j = candidates.pop(0)
        _JOBS.pop(j.id, None)


def submit_job(kind: str, fn: Callable[[Job], Any]) -> Job:
    """
    `fn(job)` をバックグラウンドで実行し Job を返す。
    """
    job_id = uuid.uuid4().hex
    job = Job(id=job_id, kind=kind)
    with _JOBS_LOCK:
        _cleanup_jobs_locked(time.time())

        inflight_total = sum(1 for j in _JOBS.values() if j.status in {"queued", "running"})
        if inflight_total >= _MAX_TOTAL_RECORDS:
            raise JobQueueFull(
                f"ジョブの in-flight 総数が上限に達しました（inflight={inflight_total}, limit={_MAX_TOTAL_RECORDS}）。"
                " 先行ジョブの完了後に再実行してください。"
            )

        queued_total = sum(1 for j in _JOBS.values() if j.status == "queued")
        if _MAX_QUEUED_JOBS > 0 and queued_total >= _MAX_QUEUED_JOBS:
            raise JobQueueFull(
                f"ジョブ待機キューが満杯です（queued={queued_total}, limit={_MAX_QUEUED_JOBS}）。"
                " しばらく待って再実行してください。"
            )

        kind_limit = _KIND_LIMITS.get(kind)
        if kind_limit and _KIND_QUEUE_MULTIPLIER > 0:
            inflight_kind = sum(1 for j in _JOBS.values() if j.kind == kind and j.status in {"queued", "running"})
            max_inflight_kind = max(1, kind_limit * _KIND_QUEUE_MULTIPLIER)
            if inflight_kind >= max_inflight_kind:
                raise JobQueueFull(
                    f"{kind} のキュー上限に達しました（inflight={inflight_kind}, limit={max_inflight_kind}）。"
                    " 先行ジョブの完了後に再実行してください。"
                )

        _JOBS[job_id] = job

    def runner() -> None:
        limiter = _KIND_SEMAPHORES.get(job.kind)
        limiter_acquired = False
        wait_started_at = time.time()
        try:
            if limiter is not None:
                with job._lock:
                    job.message = f"waiting for slot: {job.kind}"
                    job.updated_at = time.time()
                while True:
                    job.raise_if_cancel_requested()
                    if limiter.acquire(timeout=0.5):
                        limiter_acquired = True
                        break

            with job._lock:
                job.started_at = time.time()
                job.status = "running"
                job.progress = 0.0
                if limiter is not None:
                    waited = max(0.0, job.started_at - wait_started_at)
                    job.message = f"running (waited {waited:.1f}s)"
                else:
                    job.message = None
                job.error = None
                job.updated_at = job.started_at

            job.raise_if_cancel_requested()
            res = fn(job)
            if inspect.isawaitable(res):
                # NOTE: runner is executed in a worker thread (ThreadPoolExecutor),
                # so we can safely create a dedicated event loop via asyncio.run.
                async def _await_any(awaitable: Any) -> Any:
                    return await awaitable

                res = asyncio.run(_await_any(res))
            job.raise_if_cancel_requested()
            with job._lock:
                job.result = res
                job.status = "succeeded"
                job.progress = 1.0
        except JobCancelled as exc:
            with job._lock:
                job.status = "canceled"
                job.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - API で文字列化して返す
            with job._lock:
                job.status = "failed"
                job.error = str(exc)
        finally:
            if limiter_acquired:
                limiter.release()
            with job._lock:
                job.finished_at = time.time()
                job.updated_at = job.finished_at

    job._future = _EXECUTOR.submit(runner)
    return job


def get_job(job_id: str) -> Job | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def cancel_job(job_id: str) -> Job | None:
    job = get_job(job_id)
    if not job:
        return None
    job.cancel()
    # Try to cancel before it starts (best-effort).
    fut = job._future
    if fut and fut.cancel():
        with job._lock:
            job.status = "canceled"
            job.error = job.error or "canceled"
            job.finished_at = time.time()
            job.updated_at = job.finished_at
    return job


def list_jobs(
    *,
    kind: str | None = None,
    status: JobStatus | None = None,
    limit: int = 50,
) -> list[Job]:
    """
    直近ジョブ一覧を返す。

    - デフォルトは updated_at 降順（新しい順）
    - kind/status で軽く絞り込み可能
    """
    capped = max(1, min(200, int(limit)))
    with _JOBS_LOCK:
        items = list(_JOBS.values())

    if kind:
        items = [j for j in items if j.kind == kind]
    if status:
        items = [j for j in items if j.status == status]

    items.sort(key=lambda j: j.updated_at, reverse=True)
    return items[:capped]
