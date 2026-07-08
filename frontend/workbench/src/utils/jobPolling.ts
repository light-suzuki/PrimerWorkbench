import { bioapiClient } from "../api/bioapiClient";
import type { JobInfo, JobStatus } from "../types/jobs";

const TERMINAL: JobStatus[] = ["succeeded", "failed", "canceled"];

export async function pollJobUntilDone(
  jobId: string,
  opts: {
    intervalMs?: number;
    onUpdate?: (info: JobInfo) => void;
    signal?: AbortSignal;
  } = {},
): Promise<JobInfo> {
  const intervalMs = Math.max(250, opts.intervalMs ?? 900);

  for (;;) {
    if (opts.signal?.aborted) {
      throw new Error("ジョブのポーリングが中断されました。");
    }
    const info = await bioapiClient.getJob(jobId);
    opts.onUpdate?.(info);
    if (TERMINAL.includes(info.status)) return info;
    await new Promise<void>((resolve) => window.setTimeout(resolve, intervalMs));
  }
}

