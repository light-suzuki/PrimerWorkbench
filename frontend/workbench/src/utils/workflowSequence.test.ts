import { describe, expect, it } from "vitest";
import { normalizeWorkflowSequence } from "./workflowSequence";

describe("normalizeWorkflowSequence", () => {
  it("accepts a raw sequence, normalizing whitespace, case and U", () => {
    expect(normalizeWorkflowSequence(" acg u\nTTa ")).toEqual({ ok: true, sequence: "ACGTTTA" });
  });

  it("accepts a single FASTA record and drops only its header", () => {
    expect(normalizeWorkflowSequence(">one desc\nGAA\nttc\n")).toEqual({ ok: true, sequence: "GAATTC" });
  });

  it("returns an empty sequence for empty input", () => {
    expect(normalizeWorkflowSequence("")).toEqual({ ok: true, sequence: "" });
  });

  it("rejects multiple FASTA records instead of joining them (no artificial EcoRI site)", () => {
    const r = normalizeWorkflowSequence(">one\nGAA\n>two\nTTC\n");
    expect(r.ok).toBe(false);
  });

  it("rejects raw sequence followed by a FASTA header", () => {
    expect(normalizeWorkflowSequence("GAA\n>one\nTTC").ok).toBe(false);
  });

  it("rejects invalid characters instead of deleting them", () => {
    expect(normalizeWorkflowSequence("GAA!TTC").ok).toBe(false);
    expect(normalizeWorkflowSequence(">x\nGAA1TTC").ok).toBe(false);
  });
});
