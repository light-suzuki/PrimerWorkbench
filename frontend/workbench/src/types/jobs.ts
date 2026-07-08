export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "canceled";

export interface JobCreateResponse {
  job_id: string;
}

export interface JobInfo {
  job_id: string;
  kind: string;
  status: JobStatus;
  progress: number; // 0.0 - 1.0
  message?: string | null;
  error?: string | null;
  created_at: number;
  started_at?: number | null;
  finished_at?: number | null;
  updated_at: number;
}

