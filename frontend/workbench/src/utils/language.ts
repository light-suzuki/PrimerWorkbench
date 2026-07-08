import { useEffect, useState } from "react";

export type Language = "en" | "ja";
const KEY = "sequence_workbench_language";

export function currentLanguage(): Language {
  const saved = localStorage.getItem(KEY);
  if (saved === "en" || saved === "ja") return saved;
  return navigator.language.toLowerCase().startsWith("ja") ? "ja" : "en";
}

export function setLanguage(language: Language): void {
  localStorage.setItem(KEY, language);
  document.documentElement.lang = language;
  window.dispatchEvent(new CustomEvent("sequence-workbench-language", { detail: language }));
}

export function useLanguage(): [Language, (language: Language) => void] {
  const [language, update] = useState<Language>(currentLanguage);
  useEffect(() => {
    document.documentElement.lang = language;
    const listener = (event: Event) => update((event as CustomEvent<Language>).detail);
    window.addEventListener("sequence-workbench-language", listener);
    return () => window.removeEventListener("sequence-workbench-language", listener);
  }, [language]);
  return [language, setLanguage];
}
