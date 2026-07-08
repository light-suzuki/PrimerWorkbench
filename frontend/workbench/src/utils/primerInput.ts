export type ParsedFastaPrimer = { name: string; seq: string };

export const normalizePrimerSeq = (seq: string): string =>
  (seq || "")
    .replace(/\s+/g, "")
    .toUpperCase()
    .replace(/[^ACGTURYKMSWBDHVN]/g, "")
    .replace(/U/g, "T");

export const extractPrimerSeqsFromLine = (line: string): string[] => {
  const hits = (line || "").match(/[ACGTURYKMSWBDHVN]+/gi) ?? [];
  return hits
    .map((h) => normalizePrimerSeq(h))
    .filter((s) => s.length >= 10 && s.length <= 200);
};

export const parseFastaPrimers = (text: string): ParsedFastaPrimer[] => {
  const t = (text || "").trim();
  if (!t) return [];

  const out: ParsedFastaPrimer[] = [];
  let header: string | null = null;
  let seqParts: string[] = [];

  const flush = () => {
    if (!header) return;
    const seq = normalizePrimerSeq(seqParts.join(""));
    if (seq.length < 10 || seq.length > 200) return;
    const name = header.split(/\s+/)[0] || `P${out.length + 1}`;
    out.push({ name, seq });
  };

  for (const rawLine of t.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    if (line.startsWith(">")) {
      flush();
      header = line.slice(1).trim();
      seqParts = [];
      continue;
    }
    seqParts.push(line);
  }
  flush();
  return out;
};

