import type { ImportedPair } from "./primerTable";
export type OrderOligo = { name: string; sequence: string };
export const orderOligos = (pairs: ImportedPair[]): OrderOligo[] => pairs.flatMap(pair => [pair.forward, pair.reverse].map(p => ({
  name: p.name.replace(/^Pair\d+_[FR]\s+/, ""), sequence: p.sequence,
})));
export const orderWarnings = (oligos: OrderOligo[]): string[] => {
  const warnings: string[] = [];
  if (new Set(oligos.map(p => p.name.trim())).size !== oligos.length) warnings.push("同名のプライマーがあります。名前を区別してください。");
  if (new Set(oligos.map(p => p.sequence)).size !== oligos.length) warnings.push("同じ配列が複数あります。自動ではまとめません。必要な本数を確認してください。");
  if (oligos.some(p => /[^ACGT]/.test(p.sequence))) warnings.push("縮重塩基を含みます。メーカーが受け付ける記法を確認してください。");
  return warnings;
};
export const orderSheet = (oligos: OrderOligo[], scale: string, purification: string, delivery: string, note: string) => [
  ["No", "Name", "Sequence (5'-3')", "Length (nt)", "Synthesis scale", "Purification", "Delivery", "Notes"],
  ...oligos.map((p, i) => [i + 1, p.name, p.sequence, p.sequence.length, scale, purification, delivery, note]),
];
