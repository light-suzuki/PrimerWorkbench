import { expect, it, vi } from "vitest";
import { BlobReader, TextWriter, ZipReader } from "@zip.js/zip.js";
import { downloadXlsx } from "./exportXlsx";
import { orderSheet } from "./primerOrder";
it("writes actual XLSX cells with exact DNA and literal names rather than formulas", async () => {
  let exported: Blob | undefined;
  const create = vi.spyOn(URL, "createObjectURL").mockImplementation(blob => { exported = blob as Blob; return "blob:test"; });
  const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  vi.stubGlobal("document", { createElement: () => ({ click: vi.fn() }), body: { appendChild: vi.fn(), removeChild: vi.fn() } });
  try {
    await downloadXlsx([{ name: "Order", data: orderSheet([{ name: "=literal", sequence: "AACCGGTTACGT" }], "25 nmol", "", "", "") }], "primer_order");
    expect(exported?.type).toContain("spreadsheetml");
    const zip = new ZipReader(new BlobReader(exported!));
    try {
      const entries = await zip.getEntries();
      const sheet = entries.find(e => e.filename === "xl/worksheets/sheet1.xml")!;
      const xml = await sheet.getData!(new TextWriter());
      expect(xml).toContain('t="inlineStr"><is><t xml:space="preserve">=literal</t>');
      expect(xml).toContain("AACCGGTTACGT");
      expect(xml).toContain("25 nmol");
      expect(xml).not.toContain("<f>");
    } finally { await zip.close(); }
  } finally { create.mockRestore(); revoke.mockRestore(); vi.unstubAllGlobals(); }
});
