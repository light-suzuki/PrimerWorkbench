import { describe, expect, it } from "vitest";
import { parsePrimerTable, parsePrimerText, parseDelimitedTable, sequenceInCell, pairsToFasta } from "./primerTable";
const F = "ACGTACGTACGT", R = "TGCATGCATGCA", F2 = "GCTAGCTAGCTA", R2 = "TAGCTAGCTAGC";

describe("primer table import", () => {
  it("does not cross-pair different named parallel tables with opposite directions", () => {
    const result = parsePrimerTable([["one_Fw", F, "", "two_Re", R2], ["one_Re", R, "", "two_Fw", F2]]);
    expect(result.pairs.map(p => [p.forward.sequence, p.reverse.sequence])).toEqual([[F, R], [F2, R2]]);
  });
  it("prefers primer names over preceding plate well labels", () => {
    const result = parsePrimerTable([["A1", "marker_Fw", F, "12"], ["B1", "marker_Re", R, "12"]]);
    expect(result.pairs).toHaveLength(1);
    expect(result.pairs[0].forward.name).toBe("marker_Fw");
  });
  it("reads mixed horizontal fw/rv and vertical order lists with position/size columns", () => {
    const result = parsePrimerTable([["pos", "fw", "rv", "size", "", "name", "sequence"], ["123", F, R, "300", "", "marker_Fw", F2], ["", "", "", "300", "", "marker_Re", R2]]);
    expect(result.pairs.map(p => [p.forward.sequence, p.reverse.sequence])).toEqual([[F, R], [F2, R2]]);
    expect(result.unpaired).toHaveLength(0);
  });
  it("keeps numbered primer variants distinct", () => {
    const result = parsePrimerTable([["marker_fw_1", F], ["marker_fw_2", F2], ["marker_rv_2", R2], ["marker_rv_1", R]]);
    expect(result.pairs.map(p => [p.forward.sequence, p.reverse.sequence])).toEqual([[F2, R2], [F, R]]);
  });
  it("uses horizontal Fw/Re headers and ignores extra metadata", () => {
    const result = parsePrimerTable([["name", "Fw", "Re", "Tm", "description"], ["Marker1", F, R, "60.1", "restriction enzyme assay"]]);
    expect(result.pairs).toHaveLength(1);
    expect(result.pairs[0].forward.name).toBe("Marker1");
    expect(result.pairs[0].reverse.sequence).toBe(R);
  });
  it("groups named vertical records even when interleaved and reverse comes first", () => {
    const result = parsePrimerTable([["one_Re", R], ["two_Fw", F2], ["one_Fw", F], ["two_Re", R2]]);
    expect(result.pairs.map(p => [p.forward.sequence, p.reverse.sequence])).toEqual([[F, R], [F2, R2]]);
    expect(result.unpaired).toHaveLength(0);
  });
  it("defaults to independent vertical pairs for unnamed parallel columns", () => {
    const result = parsePrimerTable([[F, F2], [R, R2]]);
    expect(result.pairs.map(p => [p.forward.sequence, p.reverse.sequence])).toEqual([[F, R], [F2, R2]]);
  });
  it("can explicitly read horizontal pairs without headers", () => {
    expect(parsePrimerTable([[F, R], [F2, R2]], "rows").pairs.map(p => p.reverse.sequence)).toEqual([R, R2]);
  });
  it("handles independent named tables side by side", () => {
    const result = parsePrimerTable([["one_Fw", F, "", "two_Fw", F2], ["one_Re", R, "", "two_Re", R2]]);
    expect(result.pairs).toHaveLength(2);
    expect(result.pairs[1].reverse.name).toBe("two_Re");
  });
  it("does not combine two explicitly forward primers or hide odd records", () => {
    const result = parsePrimerTable([["one_Fw", F], ["one_Fw", F2], ["one_Re", R], ["other_Fw", R2]]);
    expect(result.pairs).toHaveLength(1);
    expect(result.unpaired).toHaveLength(2);
    expect(result.warnings.length).toBeGreaterThan(0);
  });
  it("normalizes full-width DNA and written direction without complementing", () => {
    expect(sequenceInCell("５′－ＡＣＧＴ ＡＣＧＴ ＡＣＧＴ－３′")[0].sequence).toBe(F);
    expect(sequenceInCell("3'-AACCGGTTAACC-5'")[0]).toEqual({ sequence: "CCAATTGGCCAA", reversed: true });
    expect(sequenceInCell("Tm=60.5 ng/ul 20 marker information")).toEqual([]);
  });
  it("reads quoted CSV, multiline metadata and cells separated by tabs", () => {
    expect(parseDelimitedTable(`name,Fw,Re,notes\nmarker,${F},${R},"hello,\nworld"`)[1][3]).toBe("hello,\nworld");
    expect(parsePrimerText(`name\tFw\tRe\nmarker\t${F}\t${R}`).pairs).toHaveLength(1);
  });
  it("round-trips accepted pair boundaries through FASTA despite duplicate sequences", () => {
    const pairs = parsePrimerTable([["one_Fw", F], ["one_Re", R], ["two_Fw", F], ["two_Re", R]]).pairs;
    const result = parsePrimerText(pairsToFasta(pairs));
    expect(result.pairs).toHaveLength(2);
    expect(result.pairs[0].forward.name).toContain("one_Fw");
    expect(result.pairs[1].forward.name).toContain("two_Fw");
  });
});
