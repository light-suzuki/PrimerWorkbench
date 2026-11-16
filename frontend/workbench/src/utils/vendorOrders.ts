import { BlobReader, BlobWriter, Uint8ArrayReader, Uint8ArrayWriter, ZipReader, ZipWriter } from "@zip.js/zip.js";
import type { OrderOligo } from "./primerOrder";
import { readPrimerFile } from "./primerWorkbook";

export const orderVendors = [
  { id: "eurofins", name: "Eurofins", guide: "https://eurofinsgenomics.jp/jp/contact/order-form/", templates: [
    ["標準DNA 日本語", "https://eurofinsgenomics.jp/media/29282/custom_oligo_orderform_dna_v302_std_jp.xlsx"],
    ["Standard DNA English", "https://eurofinsgenomics.jp/media/29283/custom_oligo_orderform_dna_v302_std_english.xlsx"],
  ] },
  { id: "nippon", name: "ニッポンジーン / NIPPON GENE", guide: "https://nippongene-oligo.com/support/downloads/", templates: [
    ["スタンダード 0.05 μmol", "https://nippongene-oligo.com/wp-content/themes/liquid-corporate-child/file/dna/order-standard-scale-oligo-npg.xlsx"],
    ["スモール 0.02 μmol", "https://nippongene-oligo.com/wp-content/themes/liquid-corporate-child/file/dna/order-small-scale-oligo-npg.xlsx"],
  ] },
  { id: "thermo", name: "Thermo Fisher / Invitrogen Value Oligos", guide: "https://www.thermofisher.com/order/custom-oligo/enterSequences", templates: [
    ["Value Oligos XLS", "https://www.thermofisher.com/order/custom-oligo/assets/templates/oligo_bulk-upload-template.xls"],
  ] },
  { id: "idt", name: "IDT", guide: "https://sg.idtdna.com/page/support-and-education/technical-support/how-to-order", templates: [] },
  { id: "fasmac", name: "FASMAC", guide: "https://fasmac.co.jp/dna_rna_order_flow-2", templates: [] },
  { id: "hss", name: "北海道システム・サイエンス / HSS", guide: "https://hssnet.co.jp/order/flow/", templates: [] },
  { id: "sigma", name: "Merck / Sigma-Aldrich", guide: "https://www.sigmaaldrich.com/IN/en/life-science/ecommerce/how-to-register-on-sigmaaldrich", templates: [] },
] as const;

export const thermoValueErrors = (oligos: OrderOligo[]): string[] => {
  const errors: string[] = [];
  if (!oligos.length || oligos.length > 200) errors.push("Value Oligos は1回に1〜200本です。範囲を分けてください。");
  if (oligos.some(p => !/^[ACGT]{5,40}$/.test(p.sequence))) errors.push("Value Oligos は5〜40塩基のACGTのみです。別の商品・書式を選んでください。");
  return errors;
};
export const thermoValueSheet = (oligos: OrderOligo[]) => {
  const errors = thermoValueErrors(oligos);
  if (errors.length) throw new Error(errors.join(" "));
  return { name: "Oligos", data: [["Oligo Name", "Sequence (5' to 3')  "], ...oligos.map(p => [p.name, p.sequence])] };
};

const escapeXml = (value: string) => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&apos;");
export const fillNipponCells = (xml: string, oligos: OrderOligo[]): string => {
  if (!oligos.length || oligos.length > 96) throw new Error("公式注文書は1〜96本です。範囲を分けてください。");
  let result = xml;
  oligos.forEach((p, i) => {
    if (!/^[A-Za-z0-9]{1,15}$/.test(p.name)) throw new Error("試料名は半角英数字15文字以内に編集してください。自動では短縮しません。");
    for (const [column, value] of [["G", p.name], ["H", p.sequence]]) {
      const ref = `${column}${i + 7}`;
      const pattern = new RegExp(`<c\\b[^>]*?\\br="${ref}"[^>]*?(?:/>|>[\\s\\S]*?</c>)`);
      if (!pattern.test(result)) throw new Error("注文書のセル構造が変更されています。公式書式へ手動で転記してください。");
      result = result.replace(pattern, cell => {
        const attrs = cell.slice(2, cell.indexOf(">")).replace(/\/$/, "").replace(/\s+t="[^"]*"/g, "");
        return `<c${attrs} t="inlineStr"><is><t xml:space="preserve">${escapeXml(value)}</t></is></c>`;
      });
    }
  });
  return result;
};

export async function fillNipponWorkbook(file: File, oligos: OrderOligo[]): Promise<Blob> {
  const sheets = await readPrimerFile(file);
  const order = sheets.find(s => s.name === "Order Sheet");
  if (!order || !order.rows[4]?.[6]?.includes("試料名") || !order.rows[4]?.[7]?.includes("塩基配列") || ![0.05, 0.02].includes(Number(order.rows[6]?.[5]))) throw new Error("対応する公式スタンダード／スモール注文書を選んでください。");
  if (order.rows.slice(6).some(row => row[6]?.trim() || row[7]?.trim())) throw new Error("入力済みの注文書には追記しません。公式の空の注文書を選んでください。");
  const reader = new ZipReader(new BlobReader(file));
  const writer = new ZipWriter(new BlobWriter("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"));
  try {
    const entries = await reader.getEntries();
    if (entries.reduce((n, e) => n + e.uncompressedSize, 0) > 32 * 1024 * 1024) throw new Error("展開サイズが大きすぎます。");
    let patched = false;
    for (const entry of entries) {
      if (entry.directory || !entry.getData) continue;
      let bytes = await entry.getData(new Uint8ArrayWriter());
      if (entry.filename === "xl/worksheets/sheet2.xml") {
        bytes = new TextEncoder().encode(fillNipponCells(new TextDecoder().decode(bytes), oligos)); patched = true;
      }
      if (entry.filename === "xl/workbook.xml") {
        const text = new TextDecoder().decode(bytes);
        if (!text.includes('name="Order Sheet" sheetId="2"')) throw new Error("注文書のシート構造が変更されています。");
        bytes = new TextEncoder().encode(text.replace(/<calcPr\b[^>]*\/>/, '<calcPr fullCalcOnLoad="1" forceFullCalc="1"/>'));
      }
      await writer.add(entry.filename, new Uint8ArrayReader(bytes));
    }
    if (!patched) throw new Error("注文書シートがありません。");
    return await writer.close();
  } finally { await reader.close(); }
}
