import { BlobReader, TextWriter, ZipReader } from "@zip.js/zip.js";
import { parseDelimitedTable } from "./primerTable";
export type PrimerSheet = { name: string; rows: string[][] };

const xml = (text: string) => {
  const document = new DOMParser().parseFromString(text, "application/xml");
  if (document.getElementsByTagName("parsererror").length) throw new Error("Excel XMLを読み取れませんでした。");
  return document;
};
const nodes = (node: Document | Element, name: string) => Array.from(node.getElementsByTagNameNS("*", name));
export const worksheetRows = (text: string, shared: string[]): string[][] => {
 const rows: string[][] = [];
 nodes(xml(text), "row").forEach((row, index) => {
  const rowNumber = Number(row.getAttribute("r") ?? index + 1);
  if (!Number.isInteger(rowNumber) || rowNumber < 1 || rowNumber > 100000) throw new Error("行数が多すぎます。必要な範囲をコピーしてください。");
  const values: string[] = [];
  for (const cell of nodes(row, "c")) {
    const ref = cell.getAttribute("r") ?? "";
    const letters = ref.match(/^[A-Z]+/i)?.[0] ?? "A";
    let column = 0;
    for (const letter of letters.toUpperCase()) column = column * 26 + letter.charCodeAt(0) - 64;
    if (column > 2048) throw new Error("列数が多すぎます。必要な範囲をコピーして貼り付けてください。");
    const type = cell.getAttribute("t");
    const value = nodes(cell, "v")[0]?.textContent ?? "";
    values[column - 1] = type === "s" ? shared[Number(value)] ?? "" : type === "inlineStr" ? nodes(cell, "t").map(t => t.textContent ?? "").join("") : value;
  }
  rows[rowNumber - 1] = Array.from({ length: values.length }, (_, i) => values[i] ?? "");
 });
 return Array.from({ length: rows.length }, (_, i) => rows[i] ?? []);
};

export async function readPrimerFile(file: File): Promise<PrimerSheet[]> {
  if (file.size > 20 * 1024 * 1024) throw new Error("20 MB以下のファイルか、必要な範囲の貼り付けを使ってください。");
  if (/\.(csv|tsv|txt)$/i.test(file.name)) return [{ name: file.name, rows: parseDelimitedTable(await file.text()) }];
  if (!/\.xlsx$/i.test(file.name)) throw new Error(".xlsx / CSV / TSVに対応しています。古い.xlsは.xlsxで保存するか、セルをコピーしてください。");
  const reader = new ZipReader(new BlobReader(file));
  try {
    const entries = await reader.getEntries();
    let budget = 32 * 1024 * 1024;
    const read = async (name: string) => {
      const entry = entries.find(e => e.filename === name);
      if (!entry || entry.directory || !entry.getData) return "";
      budget -= entry.uncompressedSize;
      if (budget < 0) throw new Error("Excelの展開サイズが大きすぎます。必要な範囲をコピーしてください。");
      return await entry.getData(new TextWriter()) ?? "";
    };
    const sharedText = await read("xl/sharedStrings.xml");
    const shared = sharedText ? nodes(xml(sharedText), "si").map(si => nodes(si, "t").map(t => t.textContent ?? "").join("")) : [];
    const workbook = xml(await read("xl/workbook.xml"));
    const relationships = xml(await read("xl/_rels/workbook.xml.rels"));
    const targets = new Map(nodes(relationships, "Relationship").filter(r => r.getAttribute("TargetMode") !== "External").map(r => [r.getAttribute("Id"), r.getAttribute("Target") ?? ""]));
    const sheets: PrimerSheet[] = [];
    for (const sheet of nodes(workbook, "sheet")) {
      const id = sheet.getAttributeNS("http://schemas.openxmlformats.org/officeDocument/2006/relationships", "id");
      const target = targets.get(id) ?? "";
      const parts: string[] = [];
      for (const part of (target.startsWith("/") ? target.slice(1) : "xl/" + target).split("/")) {
        if (part === "..") parts.pop(); else if (part && part !== ".") parts.push(part);
      }
      const content = await read(parts.join("/"));
      if (content) sheets.push({ name: sheet.getAttribute("name") ?? "Sheet", rows: worksheetRows(content, shared) });
    }
    if (!sheets.length) throw new Error("読取り可能なワークシートがありません。");
    return sheets;
  } finally { await reader.close(); }
}
