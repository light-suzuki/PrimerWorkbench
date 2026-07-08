import React from "react";
import type { JobInfo, JobStatus } from "../types/jobs";

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "待機中",
  running: "実行中",
  succeeded: "完了",
  failed: "失敗",
  canceled: "キャンセル",
};

export const JobProgressCard: React.FC<{
  title?: string;
  jobId?: string | null;
  job: JobInfo | null;
  onCancel?: (() => void) | null;
  cancelDisabled?: boolean;
}> = ({ title, jobId, job, onCancel, cancelDisabled }) => {
  if (!job && !jobId) return null;

  const effectiveJob: JobInfo = job ?? {
    job_id: jobId ?? "-",
    kind: "unknown",
    status: "queued",
    progress: 0.02,
    message: "starting...",
    error: null,
    created_at: Date.now() / 1000,
    started_at: null,
    finished_at: null,
    updated_at: Date.now() / 1000,
  };

  const pct = Math.max(0, Math.min(100, (effectiveJob.progress ?? 0) * 100));
  const statusText = STATUS_LABELS[effectiveJob.status] ?? effectiveJob.status;

  return (
    <div className="job-card" role="status" aria-live="polite">
      <div className="job-row">
        <div className="job-title">
          {title ? `${title}: ` : ""}
          {statusText} / {pct.toFixed(0)}%
          {effectiveJob.message ? ` - ${effectiveJob.message}` : ""}
        </div>
        {onCancel ? (
          <button
            type="button"
            className="seq-button danger"
            onClick={onCancel}
            disabled={cancelDisabled}
          >
            キャンセル
          </button>
        ) : null}
      </div>
      <div className="job-progress">
        <div className="job-progress-bar" style={{ width: `${pct}%` }} />
      </div>
      {effectiveJob.error ? <div className="seq-error">エラー: {effectiveJob.error}</div> : null}
    </div>
  );
};
