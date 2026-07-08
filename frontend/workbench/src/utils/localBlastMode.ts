import { useCallback, useEffect, useState } from "react";

export type LocalBlastMode = "cpu" | "gpu";

const STORAGE_KEY = "seqwb_local_blast_mode_v1";
const MODE_EVENT = "seqwb:local_blast_mode_changed";

const normalizeMode = (value: unknown): LocalBlastMode =>
  "cpu";

export const loadLocalBlastMode = (): LocalBlastMode => {
  if (typeof window === "undefined") return "cpu";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return "cpu";
    return normalizeMode(raw);
  } catch {
    return "cpu";
  }
};

export const saveLocalBlastMode = (mode: LocalBlastMode): void => {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, "cpu");
    window.dispatchEvent(new Event(MODE_EVENT));
  } catch {
    // ignore storage errors
  }
};

export const useLocalBlastMode = (): [LocalBlastMode, (mode: LocalBlastMode) => void] => {
  const [mode, setMode] = useState<LocalBlastMode>(() => loadLocalBlastMode());

  useEffect(() => {
    const handler = () => setMode(loadLocalBlastMode());
    window.addEventListener(MODE_EVENT, handler);
    return () => window.removeEventListener(MODE_EVENT, handler);
  }, []);

  const update = useCallback((next: LocalBlastMode) => {
    setMode(next);
    saveLocalBlastMode(next);
  }, []);

  return [mode, update];
};
