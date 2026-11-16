import { expect, it } from "vitest";
import { orderOligos, orderSheet, orderWarnings } from "./primerOrder";
it("keeps sequences and direction unchanged and removes canonical import prefixes", () => {
  const p = { name: "Pair1_F assay_Fw", sequence: "ACGTACGTACGT", source: "FASTA!R1" };
  const oligos = orderOligos([{ forward: p, reverse: { ...p, name: "Pair1_R assay_Re" } }]);
  expect(oligos.map(o => o.name)).toEqual(["assay_Fw", "assay_Re"]);
  expect(orderSheet(oligos, "", "", "", "")[1]).toEqual([1, "assay_Fw", "ACGTACGTACGT", 12, "", "", "", ""]);
  expect(orderWarnings(oligos)).toHaveLength(1);
});
it("warns on ambiguous bases and duplicate names without silently combining oligos", () => {
  expect(orderWarnings([{ name: "a", sequence: "ACGTNN" }, { name: "a", sequence: "ACGT" }])).toHaveLength(2);
});
