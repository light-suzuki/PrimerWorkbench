import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { bioapiClient } from "../api/bioapiClient";
import type { LocalBlastDbInfo } from "../types/blast";

export type LocalBlastDbOption = {
  label: string;
  value: string;
  path?: string;
  hasFasta?: boolean;
  hasAnnotation?: boolean;
};

export const CUSTOM_DB_VALUE = "__custom__";

export const DEFAULT_BLAST_DB_BASE = "~/sequence_workbench_databases";

export const query_TO_ref_VIRTUAL_DB_VALUE = "__disabled_reference_liftover__";
export const query_TO_ref_VIRTUAL_DB_LABEL = "disabled reference liftover";

export const FALLBACK_LOCAL_DB_OPTIONS: LocalBlastDbOption[] = [
  { label: "user_nucl_db", value: "user_nucl_db", path: `${DEFAULT_BLAST_DB_BASE}/user_nucl_db` },
];

export const FALLBACK_LOCAL_DB_OPTIONS_PROT: LocalBlastDbOption[] = [
  { label: "user_prot_db", value: "user_prot_db", path: `${DEFAULT_BLAST_DB_BASE}/user_prot_db` },
];

const KNOWN_ORDER = [
  "user_nucl_db",
  "user_prot_db",
];

export const withqueryTorefVirtualDbOption = (
  options: LocalBlastDbOption[],
): LocalBlastDbOption[] => {
  return options;
};

const sortDbOptions = (options: LocalBlastDbOption[]): LocalBlastDbOption[] => {
  const rank = (label: string): number => {
    const idx = KNOWN_ORDER.indexOf(label.toLowerCase());
    return idx >= 0 ? idx : 999;
  };
  return options
    .slice()
    .sort((a, b) => {
      const ra = rank(a.label);
      const rb = rank(b.label);
      if (ra !== rb) return ra - rb;
      return a.label.localeCompare(b.label);
    });
};

const toOption = (info: LocalBlastDbInfo): LocalBlastDbOption => ({
  label: info.label,
  value: info.label,
  path: info.path,
  hasFasta: info.has_fasta,
  hasAnnotation: info.has_annotation,
});

export const normalizeLocalDbValue = (raw: string): string => {
  const s = (raw ?? "").trim();
  if (!s) return "";
  if (s === query_TO_ref_VIRTUAL_DB_VALUE) return s;
  if (s === CUSTOM_DB_VALUE) return s;

  const norm = s.replace(/\\/g, "/");
  const looksLikeWorkbenchDb =
    norm.startsWith(`${DEFAULT_BLAST_DB_BASE}/`) ||
    norm.includes("/sequence_workbench_databases/") ||
    norm.includes("/blast_databases/");
  const stripGpuSuffix = (name: string): string => {
    const n = name.trim();
    if (n.toLowerCase().endsWith("_ps")) return n.slice(0, -3);
    return n;
  };
  if (!looksLikeWorkbenchDb) {
    // 旧データで _ps を明示的に選択しているケースを吸収
    if (!norm.includes("/") && !norm.startsWith("__")) return stripGpuSuffix(s);
    return s;
  }
  const base = norm.split("/").filter(Boolean).pop() ?? s;
  return stripGpuSuffix(base);
};

export const withCustomDbOption = (
  options: LocalBlastDbOption[],
  label = "手動入力（任意の makeblastdb パス）",
): LocalBlastDbOption[] => [...options, { label, value: CUSTOM_DB_VALUE }];

export const labelForDbPath = (
  path: string,
  options?: LocalBlastDbOption[] | null,
): string => {
  const hit = (options ?? []).find((o) => o.value === path || o.path === path);
  if (hit?.label) return hit.label;
  return path.split(/[/\\]/).filter(Boolean).pop() ?? path;
};

export const buildLocalSourceLabelMap = (
  dbPaths: string[],
  options?: LocalBlastDbOption[] | null,
): Map<string, string> => {
  const map = new Map<string, string>();
  dbPaths.forEach((p) => {
    const name = p.split(/[/\\]/).filter(Boolean).pop() ?? p;
    map.set(name, labelForDbPath(p, options));
  });
  return map;
};

export const relabelLocalBlastHits = <T extends { source?: string | null }>(
  hits: T[],
  dbPaths: string[],
  options?: LocalBlastDbOption[] | null,
): T[] => {
  if (!hits.length) return hits;
  const map = buildLocalSourceLabelMap(dbPaths, options);
  const hasSingle = map.size === 1;
  const singleLabel = hasSingle ? Array.from(map.values())[0] : null;

  return hits.map((h) => {
    const src = h.source ?? "";
    if (src === "local" && singleLabel) {
      return { ...h, source: `local:${singleLabel}` };
    }
    if (!src.startsWith("local:")) return h;
    const key = src.slice("local:".length);
    const label = map.get(key);
    if (!label || label === key) return h;
    return { ...h, source: `local:${label}` };
  });
};

export const defaultSelectedDbPaths = (
  options: LocalBlastDbOption[],
  count = 3,
): string[] => {
  const byLabel = new Map(options.map((o) => [o.label.toLowerCase(), o.value]));
  const picked: string[] = [];
  for (const lab of KNOWN_ORDER) {
    const v = byLabel.get(lab);
    if (v) picked.push(v);
  }
  if (picked.length > 0) return picked.slice(0, count);
  return options.slice(0, count).map((o) => o.value);
};

export const useLocalBlastDbOptions = (): {
  options: LocalBlastDbOption[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
} => {
  return useLocalBlastDbOptionsByType("nucl");
};

export const useLocalBlastDbOptionsByType = (
  dbType: "nucl" | "prot" = "nucl",
): {
  options: LocalBlastDbOption[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
} => {
  const [options, setOptions] = useState<LocalBlastDbOption[]>(
    dbType === "prot" ? FALLBACK_LOCAL_DB_OPTIONS_PROT : FALLBACK_LOCAL_DB_OPTIONS,
  );
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
      setError(null);
      try {
        const dbs = await bioapiClient.listLocalBlastDbs(dbType);
        const mapped = (dbs ?? []).map(toOption);
        const labelSet = new Set(mapped.map((o) => o.label.toLowerCase()));
        const filtered = mapped.filter((o) => {
          const lab = (o.label || "").trim();
          if (!lab.toLowerCase().endsWith("_ps")) return true;
          const base = lab.slice(0, -3);
          return !labelSet.has(base.toLowerCase());
        });
        if (filtered.length > 0) {
          setOptions(sortDbOptions(filtered));
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "ローカル DB の取得に失敗しました。");
      } finally {
        setLoading(false);
    }
  }, [dbType]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const normalized = useMemo(() => sortDbOptions(options), [options]);

  return { options: normalized, loading, error, refresh };
};

const PREFERRED_LOCAL_DBS_STORAGE_KEY = "seqwb_preferred_local_blast_dbs_v1";
const PREFERRED_LOCAL_DBS_EVENT = "seqwb:preferred_local_blast_dbs_changed";

const normalizeDbPathList = (value: unknown): string[] => {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  value.forEach((v) => {
    if (typeof v !== "string") return;
    const s = normalizeLocalDbValue(v);
    if (!s) return;
    if (seen.has(s)) return;
    seen.add(s);
    out.push(s);
  });
  return out;
};

export const loadPreferredLocalDbPaths = (): string[] | null => {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(PREFERRED_LOCAL_DBS_STORAGE_KEY);
    if (raw == null) return null;
    const parsed = JSON.parse(raw) as unknown;
    return normalizeDbPathList(parsed);
  } catch {
    return null;
  }
};

export const savePreferredLocalDbPaths = (paths: string[]): void => {
  if (typeof window === "undefined") return;
  try {
    const normalized = normalizeDbPathList(paths);
    window.localStorage.setItem(PREFERRED_LOCAL_DBS_STORAGE_KEY, JSON.stringify(normalized));
    window.dispatchEvent(new Event(PREFERRED_LOCAL_DBS_EVENT));
  } catch {
    // localStorage が使えない環境では黙殺
  }
};

export const usePreferredLocalDbPaths = (
  fallback?: string[],
): readonly [
  string[],
  Dispatch<SetStateAction<string[]>>,
  () => void,
] => {
  const fallbackPaths = useMemo(() => {
    const raw = fallback && fallback.length ? fallback : FALLBACK_LOCAL_DB_OPTIONS.map((o) => o.value);
    return normalizeDbPathList(raw);
  }, [fallback]);

  const [selected, setSelectedState] = useState<string[]>(() => {
    const stored = loadPreferredLocalDbPaths();
    return stored ?? fallbackPaths;
  });

  useEffect(() => {
    if (typeof window === "undefined") return;

    const sync = () => {
      const stored = loadPreferredLocalDbPaths();
      setSelectedState(stored ?? fallbackPaths);
    };

    const onStorage = (e: StorageEvent) => {
      if (e.key !== PREFERRED_LOCAL_DBS_STORAGE_KEY) return;
      sync();
    };

    window.addEventListener("storage", onStorage);
    window.addEventListener(PREFERRED_LOCAL_DBS_EVENT, sync as EventListener);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(PREFERRED_LOCAL_DBS_EVENT, sync as EventListener);
    };
  }, [fallbackPaths]);

  const setSelected: Dispatch<SetStateAction<string[]>> = useCallback(
    (next) => {
      setSelectedState((prev) => {
        const resolved = typeof next === "function" ? (next as (p: string[]) => string[])(prev) : next;
        const normalized = normalizeDbPathList(resolved);
        savePreferredLocalDbPaths(normalized);
        return normalized;
      });
    },
    [],
  );

  const reset = useCallback(() => {
    setSelectedState(fallbackPaths);
    savePreferredLocalDbPaths(fallbackPaths);
  }, [fallbackPaths]);

  return [selected, setSelected, reset] as const;
};

