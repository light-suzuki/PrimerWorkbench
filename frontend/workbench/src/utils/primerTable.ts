export type PrimerLayout = "auto" | "rows" | "columns";
export type ImportedPrimer = { sequence: string; name: string; source: string; direction?: "F" | "R" };
export type ImportedPair = { forward: ImportedPrimer; reverse: ImportedPrimer };
export type PrimerTableResult = { pairs: ImportedPair[]; unpaired: ImportedPrimer[]; warnings: string[] };
const clean = (text: string) => text.normalize("NFKC").trim();

const direction = (text: string): "F" | "R" | undefined => {
  const value = clean(text).toLowerCase().replace(/[\s_.-]/g, "");
  if (/^(f|fw|fwd|forward|left|sense|正向|順方向)$/.test(value)) return "F";
  if (/^(r|re|rv|rev|reverse|right|antisense|逆向|逆方向)$/.test(value)) return "R";
  const suffix = clean(text).match(/(?:^|[\s_.-])(fw|fwd|forward|f|re|rv|rev|reverse|r)(?:[_.-]?(\d+))?$/i);
  return suffix ? (/^f/i.test(suffix[1]) ? "F" : "R") : undefined;
};
const family = (text: string) => clean(text).replace(/(?:[\s_.-])(?:fw|fwd|forward|f|re|rv|rev|reverse|r)(?:[_.-]?(\d+))?$/i, (_, variant: string | undefined) => variant ? `_${variant}` : "").toLowerCase();

export const sequenceInCell = (raw: string): { sequence: string; reversed: boolean }[] => {
  let value = clean(raw).replace(/[′’]/g, "'");
  const reversed = /^3\s*['′]?\s*[-–]?/i.test(value) && /5\s*['′]?\s*$/.test(value);
  value = value.replace(/^\s*(?:sequence|seq|配列|fw|fwd|forward|re|rev|reverse|f|r)\s*[:=：]\s*/i, "")
    .replace(/^[35]\s*'\s*[-–]?\s*/, "").replace(/\s*[-–]?\s*[35]\s*'$/, "");
  const normalize = (text: string) => text.replace(/[\s-]/g, "").toUpperCase().replace(/U/g, "T");
  const valid = (text: string) => /^[ACGTRYSWKMBDHVN]{10,200}$/.test(text) && /[ACGT]/.test(text);
  const compact = normalize(value);
  const sequences = valid(compact) ? [compact] : value.split(/[\s,;|:=]+/).map(normalize).filter(valid);
  return sequences.map(sequence => ({ sequence: reversed ? sequence.split("").reverse().join("") : sequence, reversed }));
};

export const parseDelimitedTable = (text: string): string[][] => {
  const input = text.replace(/^\ufeff/, "");
  const delimiter = input.includes("\t") ? "\t" : input.includes("|") ? "|" : input.includes(",") ? "," : input.includes(";") ? ";" : "\t";
  const rows: string[][] = []; let row: string[] = []; let cell = ""; let quoted = false;
  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    if (ch === '"') {
      if (quoted && input[i + 1] === '"') { cell += '"'; i++; }
      else quoted = !quoted;
    } else if (!quoted && ch === delimiter) { row.push(cell); cell = ""; }
    else if (!quoted && (ch === "\n" || ch === "\r")) {
      if (ch === "\r" && input[i + 1] === "\n") i++;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += ch;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows;
};

export const parsePrimerTable = (rows: string[][], layout: PrimerLayout = "auto", sheet = "paste"): PrimerTableResult => {
  const result: PrimerTableResult = { pairs: [], unpaired: [], warnings: [] };
  const pending = new Map<string, ImportedPrimer>();
  const headers = new Map<number, "F" | "R">();
  const addPair = (a: ImportedPrimer, b: ImportedPrimer): boolean => {
    if (a.direction && b.direction && a.direction === b.direction) {
      result.unpaired.push(a, b); result.warnings.push(`${a.source}: 同じ方向の2本をペアにしませんでした。`); return false;
    }
    const reverseFirst = a.direction === "R" || b.direction === "F";
    const forward = reverseFirst ? b : a, reverse = reverseFirst ? a : b;
    const index = result.pairs.length + 1;
    result.pairs.push({ forward: { ...forward, name: forward.name || `Pair${index}_F` }, reverse: { ...reverse, name: reverse.name || `Pair${index}_R` } });
    return true;
  };
  const enqueue = (primer: ImportedPrimer, column: number) => {
    const key = `${column}:${primer.name ? family(primer.name) : "unnamed"}`;
    const previous = pending.get(key);
    if (!previous) { pending.set(key, primer); return; }
    if (previous.direction && primer.direction && previous.direction === primer.direction) {
      result.unpaired.push(previous); result.warnings.push(`${previous.source}: 対応する逆方向の配列がありません。`);
      pending.set(key, primer); return;
    }
    pending.delete(key); addPair(previous, primer);
  };
  rows.forEach((cells, rowIndex) => {
    const found = cells.flatMap((cell, column) => sequenceInCell(cell).map((hit, token) => ({ ...hit, column, cell, token })));
    if (!found.length) {
      cells.forEach((cell, column) => { const d = direction(cell); if (d) headers.set(column, d); });
      return;
    }
    const primers = found.map(hit => {
      const previousColumn = found.filter(other => other.column < hit.column).at(-1)?.column ?? -1;
      const metadata = cells.slice(previousColumn + 1, hit.column).map(clean).filter(Boolean);
      const names = metadata.filter(value => (!direction(value) || family(value) !== value.toLowerCase()) && !/^\d+(?:\.\d+)?$/.test(value) && !/^(name|neme|no-name|名前|名称|id)$/i.test(value));
      const name = names.find(value => direction(value)) ?? names[0] ?? "";
      const d = direction(hit.cell.split(/[:=：]/)[0]) ?? metadata.map(direction).find(Boolean) ?? direction(name) ?? headers.get(hit.column);
      if (hit.reversed) result.warnings.push(`${sheet}!R${rowIndex + 1}C${hit.column + 1}: 3′→5′の記載を逆順にして5′→3′へ変換しました。`);
      return { column: hit.column, primer: { sequence: hit.sequence, name, direction: d, source: `${sheet}!R${rowIndex + 1}C${hit.column + 1}` } as ImportedPrimer };
    });
    let i = 0;
    while (i < primers.length) {
      const a = primers[i], b = primers[i + 1];
      const headerPair = b && headers.get(a.column) && headers.get(b.column) && headers.get(a.column) !== headers.get(b.column);
      const sameName = b && (!a.primer.name || !b.primer.name || family(a.primer.name) === family(b.primer.name));
      const inlinePair = b && ((sameName && a.primer.direction && b.primer.direction && a.primer.direction !== b.primer.direction) || a.column === b.column);
      const horizontal = layout === "rows" || (layout === "auto" && (headerPair || inlinePair));
      if (horizontal && b) { addPair(a.primer, b.primer); i += 2; }
      else { enqueue(a.primer, a.column); i++; }
    }
  });
  result.unpaired.push(...pending.values());
  if (result.unpaired.length) result.warnings.push(`${result.unpaired.length}本はペア未確定です。検索には含めません。`);
  if (layout === "auto" && result.pairs.length) result.warnings.push("自動読取りです。名前・方向・ペアの組み合わせを確認してください。方向表記のない表は各列で上下ペアと解釈します。横ペアの表は読み方を切り替えてください。");
  return result;
};

export const parsePrimerText = (text: string, layout: PrimerLayout = "auto"): PrimerTableResult => {
  if (!text.trim().startsWith(">")) return parsePrimerTable(parseDelimitedTable(text), layout);
  const rows: string[][] = []; let name = "", sequence = "";
  const flush = () => { if (name || sequence) rows.push([name, sequence]); };
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith(">")) { flush(); name = line.slice(1).trim(); sequence = ""; }
    else sequence += line.trim();
  }
  flush();
  // FASTA records use explicit direction/name when available, otherwise adjacency.
  const named = rows.map(([name, seq]) => {
    const token = name.split(/\s+/)[0];
    return [direction(token) ? token : direction(name) ? name : "", seq];
  });
  const parsed = parsePrimerTable(named, "columns", "FASTA");
  parsed.pairs.forEach(pair => [pair.forward, pair.reverse].forEach(p => {
    const row = Number(p.source.match(/!R(\d+)/)?.[1]);
    p.name = rows[row - 1]?.[0] || p.name;
  }));
  return parsed;
};

export const pairsToFasta = (pairs: ImportedPair[]) => pairs.flatMap((pair, i) => [
  `>Pair${i + 1}_F ${pair.forward.name}\n${pair.forward.sequence}`,
  `>Pair${i + 1}_R ${pair.reverse.name}\n${pair.reverse.sequence}`,
]).join("\n");
