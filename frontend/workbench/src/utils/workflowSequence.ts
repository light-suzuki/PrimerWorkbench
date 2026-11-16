// Single-sequence normalization for the Workflow panel.
//
// The Workflow sequence field accepts one FASTA record or one raw sequence.
// Multiple records, raw text mixed with a FASTA header, and non-IUPAC
// characters are rejected instead of being silently joined or dropped, which
// would fabricate artificial junctions (restriction sites, ORFs).

export type WorkflowSequenceResult =
  | { ok: true; sequence: string }
  | { ok: false; error: string };

export const normalizeWorkflowSequence = (text: string): WorkflowSequenceResult => {
  const lines = (text || "").split(/\r?\n/);
  let headerCount = 0;
  let seenSequence = false;
  const parts: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith(">")) {
      headerCount += 1;
      if (headerCount > 1) {
        return {
          ok: false,
          error: "複数の FASTA レコードが含まれています。1 配列だけを貼り付けてください（レコードを結合しません）。",
        };
      }
      if (seenSequence) {
        return {
          ok: false,
          error: "FASTA ヘッダーの前に配列があります。生配列と FASTA を混在させず、1 配列だけを貼り付けてください。",
        };
      }
      continue;
    }
    seenSequence = true;
    parts.push(trimmed.replace(/\s+/g, ""));
  }

  const upper = parts.join("").toUpperCase();
  const invalid = upper.replace(/[ACGTURYKMSWBDHVN]/g, "");
  if (invalid) {
    const sample = Array.from(new Set(invalid.split(""))).slice(0, 5).join(" ");
    return {
      ok: false,
      error: `塩基配列として無効な文字が含まれています（${sample}）。文字を除去せず、入力を修正してください。`,
    };
  }
  return { ok: true, sequence: upper.replace(/U/g, "T") };
};
