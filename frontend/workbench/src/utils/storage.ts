import { useEffect, useState } from "react";

export const readLocal = (key: string): string | null => {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
};

export const writeLocal = (key: string, value: string): void => {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // ignore
  }
};

export const readLocalJson = <T>(key: string, fallback: T): T => {
  const raw = readLocal(key);
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
};

export const writeLocalJson = <T>(key: string, value: T): void => {
  try {
    writeLocal(key, JSON.stringify(value));
  } catch {
    // ignore
  }
};

export const useLocalStorageFlag = (
  key: string,
  defaultValue: boolean,
): [boolean, (next: boolean | ((prev: boolean) => boolean)) => void] => {
  const [value, setValue] = useState<boolean>(() => {
    const raw = readLocal(key);
    if (raw == null) return defaultValue;
    return raw === "1" || raw.toLowerCase() === "true";
  });

  useEffect(() => {
    writeLocal(key, value ? "1" : "0");
  }, [key, value]);

  return [value, setValue];
};

