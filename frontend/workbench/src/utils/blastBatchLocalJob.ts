import { bioapiClient } from "../api/bioapiClient";
import type { BlastBatchLocalRequest, BlastBatchLocalResponse, BlastResponse } from "../types/blast";
import type { JobInfo } from "../types/jobs";
import { pollJobUntilDone } from "./jobPolling";

export async function runBlastBatchLocalJob(
  body: BlastBatchLocalRequest,
  opts: {
    onCreated?: (jobId: string) => void;
    onUpdate?: (info: JobInfo) => void;
    intervalMs?: number;
    signal?: AbortSignal;
  } = {},
): Promise<{ jobId: string; jobInfo: JobInfo; result: BlastBatchLocalResponse }> {
  const job = await bioapiClient.createBlastBatchLocalJob(body);
  opts.onCreated?.(job.job_id);
  const info = await pollJobUntilDone(job.job_id, {
    onUpdate: opts.onUpdate,
    intervalMs: opts.intervalMs,
    signal: opts.signal,
  });
  if (info.status !== "succeeded") {
    throw new Error(info.error ?? "ローカル BLAST ジョブに失敗しました。");
  }
  const res = await bioapiClient.getJobResult<{ results: BlastResponse[] }>(job.job_id);
  return { jobId: job.job_id, jobInfo: info, result: { results: res.results ?? [] } };
}
