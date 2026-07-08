export type ApiResponseType = "json" | "text" | "arrayBuffer" | "void";

export class ApiError extends Error {
  status: number;
  statusText: string;
  url: string;
  detail: string;

  constructor(args: { status: number; statusText: string; url: string; detail: string }) {
    super(`API request failed: ${args.status} ${args.statusText} - ${args.detail}`);
    this.name = "ApiError";
    this.status = args.status;
    this.statusText = args.statusText;
    this.url = args.url;
    this.detail = args.detail;
  }
}

const DEFAULT_TIMEOUT_MS = 60_000;

function joinUrl(baseUrl: string, path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${baseUrl}${path}`;
}

function extractDetail(rawBody: string, contentType: string | null): string {
  const body = (rawBody ?? "").trim();
  if (!body) return "";

  const ct = (contentType ?? "").toLowerCase();
  if (!ct.includes("application/json")) return body;

  try {
    const parsed = JSON.parse(body) as unknown;
    if (parsed && typeof parsed === "object" && "detail" in (parsed as any)) {
      const detail = (parsed as any).detail;
      if (typeof detail === "string") return detail;
      return JSON.stringify(detail);
    }
    return JSON.stringify(parsed);
  } catch {
    return body;
  }
}

async function request<T>(
  baseUrl: string,
  path: string,
  opts: {
    method?: "GET" | "POST" | "DELETE";
    headers?: Record<string, string>;
    body?: unknown;
    responseType?: ApiResponseType;
    timeoutMs?: number;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const url = joinUrl(baseUrl, path);
  const controller = new AbortController();

  const timeoutMs = Math.max(1, opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const timeoutId = globalThis.setTimeout(() => controller.abort(), timeoutMs);

  if (opts.signal) {
    if (opts.signal.aborted) controller.abort();
    else opts.signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  try {
    const response = await fetch(url, {
      method: opts.method ?? "GET",
      headers: opts.headers,
      body: opts.body == null ? undefined : (opts.body as BodyInit),
      signal: controller.signal,
    });

    if (!response.ok) {
      const raw = await response.text().catch(() => "");
      const detail = extractDetail(raw, response.headers.get("content-type")) || raw || response.statusText;
      throw new ApiError({
        status: response.status,
        statusText: response.statusText,
        url,
        detail,
      });
    }

    const responseType = opts.responseType ?? "json";
    if (responseType === "void") return undefined as T;
    if (responseType === "text") return (await response.text()) as T;
    if (responseType === "arrayBuffer") return (await response.arrayBuffer()) as T;
    return (await response.json()) as T;
  } catch (err: any) {
    if (err?.name === "AbortError") {
      throw new Error(`API request timed out (${timeoutMs}ms): ${url}`);
    }
    throw err;
  } finally {
    globalThis.clearTimeout(timeoutId);
  }
}

export function apiGetJson<T>(baseUrl: string, path: string, opts?: { timeoutMs?: number; signal?: AbortSignal }): Promise<T> {
  return request<T>(baseUrl, path, { method: "GET", responseType: "json", ...opts });
}

export function apiPostJson<TBody extends object, TResponse>(
  baseUrl: string,
  path: string,
  body: TBody,
  opts?: { timeoutMs?: number; signal?: AbortSignal },
): Promise<TResponse> {
  return request<TResponse>(baseUrl, path, {
    method: "POST",
    responseType: "json",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...opts,
  });
}

export function apiDelete(baseUrl: string, path: string, opts?: { timeoutMs?: number; signal?: AbortSignal }): Promise<void> {
  return request<void>(baseUrl, path, { method: "DELETE", responseType: "void", ...opts });
}

export function apiGetText(baseUrl: string, path: string, opts?: { timeoutMs?: number; signal?: AbortSignal }): Promise<string> {
  return request<string>(baseUrl, path, { method: "GET", responseType: "text", ...opts });
}

export function apiGetBinary(baseUrl: string, path: string, opts?: { timeoutMs?: number; signal?: AbortSignal }): Promise<ArrayBuffer> {
  return request<ArrayBuffer>(baseUrl, path, { method: "GET", responseType: "arrayBuffer", ...opts });
}
