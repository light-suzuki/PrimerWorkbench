import { expect, it } from "vitest";
import { fillNipponCells, thermoValueErrors, thermoValueSheet } from "./vendorOrders";
it("matches verified Invitrogen headers and blocks unsupported products rather than truncating", () => {
  expect(thermoValueSheet([{ name: "x", sequence: "ACGTACGT" }])).toEqual({ name: "Oligos", data: [["Oligo Name", "Sequence (5' to 3')  "], ["x", "ACGTACGT"]] });
  expect(thermoValueErrors([{ name: "x", sequence: "ACGTN" }])).toHaveLength(1);
  expect(thermoValueErrors(Array.from({ length: 201 }, () => ({ name: "x", sequence: "ACGTA" })))).toHaveLength(1);
  expect(() => thermoValueSheet([{ name: "x", sequence: "A".repeat(41) }])).toThrow();
});
it("preserves vendor cell styles and formulas while filling only the name and sequence", () => {
  const xml = '<row r="7"><c r="A7"><f>LEN(H7)</f><v>0</v></c><c r="G7" s="12"/><c r="H7" s="13"></c></row>';
  const filled = fillNipponCells(xml, [{ name: "Assay1F", sequence: "ACGTACGT" }]);
  expect(filled).toContain('<c r="G7" s="12" t="inlineStr">');
  expect(filled).toContain("Assay1F"); expect(filled).toContain("ACGTACGT");
  expect(filled).toContain('<c r="A7"><f>LEN(H7)</f><v>0</v></c>');
  expect(() => fillNipponCells(xml, [{ name: "too_long_or_symbol", sequence: "ACGT" }])).toThrow();
  expect(() => fillNipponCells(xml, [{ name: "x", sequence: "ACGT" }, { name: "y", sequence: "ACGT" }])).toThrow();
});
