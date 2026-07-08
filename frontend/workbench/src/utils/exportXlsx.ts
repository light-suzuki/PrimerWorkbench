import { BlobWriter, TextReader, ZipWriter } from "@zip.js/zip.js";

export type XlsxSheet = {
  name: string;
  data: Array<Array<string | number | null | undefined>>;
};

const xml = (value: unknown): string =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");

const columnName = (index: number): string => {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
};

export const downloadXlsx = async (sheets: XlsxSheet[], baseName: string): Promise<void> => {
  if (!sheets.length) return;
  const names = sheets.map((sheet, index) =>
    (sheet.name || `Sheet${index + 1}`).replace(/[\\/*?:[\]]/g, "_").slice(0, 31),
  );
  const writer = new ZipWriter(
    new BlobWriter("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
  );
  const add = (path: string, content: string) => writer.add(path, new TextReader(content));
  await add("[Content_Types].xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
${sheets.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("")}
</Types>`);
  await add("_rels/.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>`);
  await add("xl/workbook.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>${names.map((name, i) => `<sheet name="${xml(name)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join("")}</sheets>
</workbook>`);
  await add("xl/_rels/workbook.xml.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
${sheets.map((_, i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join("")}
</Relationships>`);
  for (const [sheetIndex, sheet] of sheets.entries()) {
    const rows = sheet.data.map((row, rowIndex) => {
      const cells = row.map((value, columnIndex) => {
        const ref = `${columnName(columnIndex)}${rowIndex + 1}`;
        return typeof value === "number"
          ? `<c r="${ref}"><v>${value}</v></c>`
          : `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${xml(value)}</t></is></c>`;
      }).join("");
      return `<row r="${rowIndex + 1}">${cells}</row>`;
    }).join("");
    await add(`xl/worksheets/sheet${sheetIndex + 1}.xml`, `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${rows}</sheetData></worksheet>`);
  }
  const blob = await writer.close();
  const a = document.createElement("a");
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const safeBase = baseName || "report";
  a.download = `${safeBase}_${ts}.xlsx`;
  a.href = URL.createObjectURL(blob);
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
};

