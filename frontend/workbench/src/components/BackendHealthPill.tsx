import React, { useEffect, useMemo, useState } from "react";
import { bioapiClient } from "../api/bioapiClient";

type HealthState = "checking" | "ok" | "offline";

const formatTs = (ts: number): string => {
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return String(ts);
  }
};

export const BackendHealthPill: React.FC = () => {
  const [state, setState] = useState<HealthState>("checking");
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [lastCheckedAt, setLastCheckedAt] = useState<number | null>(null);

  const docsUrl = useMemo(() => {
    if (typeof window === "undefined") return "http://localhost:8000/docs";
    return "/docs";
  }, []);

  useEffect(() => {
    let cancelled = false;

    const ping = async () => {
      const t0 = typeof performance !== "undefined" ? performance.now() : Date.now();
      try {
        const res = await bioapiClient.health();
        if (cancelled) return;
        if (res && res.status === "ok") {
          setState("ok");
          const t1 = typeof performance !== "undefined" ? performance.now() : Date.now();
          setLatencyMs(Math.round(t1 - t0));
        } else {
          setState("offline");
          setLatencyMs(null);
        }
      } catch {
        if (cancelled) return;
        setState("offline");
        setLatencyMs(null);
      } finally {
        if (!cancelled) setLastCheckedAt(Date.now());
      }
    };

    ping();
    const id = window.setInterval(ping, 8000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const { color, text } = useMemo(() => {
    if (state === "ok") {
      return {
        color: "#16a34a",
        text: `BioAPI: OK${latencyMs != null ? ` (${latencyMs}ms)` : ""}`,
      };
    }
    if (state === "offline") {
      return { color: "#dc2626", text: "BioAPI: offline" };
    }
    return { color: "#94a3b8", text: "BioAPI: checking" };
  }, [latencyMs, state]);

  const title = lastCheckedAt ? `last checked: ${formatTs(lastCheckedAt)}` : "checking...";

  return (
    <a className="hero-pill hero-pill-link" href={docsUrl} target="_blank" rel="noreferrer" title={`${title} / click to open docs`}>
      <span className="status-dot" style={{ background: color }} />
      {text}
    </a>
  );
};
