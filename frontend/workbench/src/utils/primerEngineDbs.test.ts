import { describe, expect, it } from "vitest";
import { engineDbKey, isEngineDbSelected, removeEngineDbSelection, resolveEngineDbs } from "./primerEngineDbs";

describe("embedded engine references", () => {
  it("shows normalized saved selections and sends the actual registered prefix", () => {
    const options = [{ label: "synthetic", value: "synthetic", path: "~/sequence_workbench_databases/synthetic" }];
    expect(engineDbKey(options[0])).toBe("synthetic");
    expect(resolveEngineDbs(["synthetic"], options, "")).toEqual([options[0].path]);
  });
  it("keeps a custom prefix literal and deduplicates it", () => {
    expect(resolveEngineDbs([".runtime/synthetic"], [], " .runtime/synthetic ")).toEqual([".runtime/synthetic"]);
    expect(resolveEngineDbs([], [], " .runtime/synthetic ")).toEqual([".runtime/synthetic"]);
  });
  it("recognizes saved labels and removes both a label and its prefix when unchecked", () => {
    const option = { label: "synthetic", value: "synthetic", path: "/references/synthetic" };
    expect(isEngineDbSelected(["synthetic"], option)).toBe(true);
    expect(removeEngineDbSelection(["synthetic", option.path, "other"], option)).toEqual(["other"]);
    expect(resolveEngineDbs(["synthetic", option.path], [option], "")).toEqual([option.path]);
  });
});
