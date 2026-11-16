import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ensemblGeneUrl,
  geneUrlForContext,
  isLocalOnlyDb,
} from "./ensembl";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("geneUrlForContext", () => {
  it("Ensembl gene fixture は species 設定時に正しい外部リンクを返す", () => {
    vi.stubEnv("VITE_ENSEMBL_SPECIES", "vicia_faba");
    vi.stubEnv("VITE_LOCAL_REFERENCE_BASE_URL", "");
    const url = geneUrlForContext({
      geneId: "Vfaba.GeneID.00001",
      dbLabel: "/home/user/blastdb/ref_genome",
    });
    expect(url).toBe(
      "https://plants.ensembl.org/vicia_faba/Gene/Summary?g=Vfaba.GeneID.00001",
    );
  });

  it("species 未設定でも Ensembl 検索フォールバックは従来どおり返す", () => {
    vi.stubEnv("VITE_ENSEMBL_SPECIES", "");
    const url = ensemblGeneUrl("GENE05G12345");
    expect(url).toMatch(/^https:\/\/plants\.ensembl\.org\/Multi\/Search\/Results/);
  });

  it("local-only gene は Ensembl へリンクしない（base URL 設定時はローカル参照ブラウザ）", () => {
    vi.stubEnv("VITE_ENSEMBL_SPECIES", "");
    vi.stubEnv("VITE_LOCAL_REFERENCE_BASE_URL", "http://127.0.0.1:8000");
    vi.stubEnv("VITE_LOCAL_REFERENCE_SPECIES", "local_genome");
    const url = geneUrlForContext({
      geneId: "contig123_gene456",
      dbLabel: "/home/user/blastdb/local_only",
    });
    expect(url).toBe(
      "http://127.0.0.1:8000/local_genome/Gene/Summary?g=contig123_gene456",
    );
  });

  it("local-only gene で base URL 未設定なら null（plain text で出す）", () => {
    vi.stubEnv("VITE_ENSEMBL_SPECIES", "");
    vi.stubEnv("VITE_LOCAL_REFERENCE_BASE_URL", "");
    const url = geneUrlForContext({
      geneId: "local_contig_gene",
      dbLabel: "/home/user/blastdb/local_only",
    });
    expect(url).toBeNull();
  });

  it("link 不能 ID（空/未指定）は null を返す", () => {
    vi.stubEnv("VITE_ENSEMBL_SPECIES", "vicia_faba");
    expect(geneUrlForContext({ geneId: null, dbLabel: "x" })).toBeNull();
    expect(geneUrlForContext({ geneId: "  ", dbLabel: "x" })).toBeNull();
  });

  it("dbLabel が空でも species 設定時は Ensembl リンクを返す", () => {
    vi.stubEnv("VITE_ENSEMBL_SPECIES", "vicia_faba");
    expect(geneUrlForContext({ geneId: "Vfaba.GeneID.00001" })).toBe(
      "https://plants.ensembl.org/vicia_faba/Gene/Summary?g=Vfaba.GeneID.00001",
    );
  });
});

describe("isLocalOnlyDb", () => {
  it("species 未設定・dbLabel ありは local-only", () => {
    vi.stubEnv("VITE_ENSEMBL_SPECIES", "");
    expect(isLocalOnlyDb("/home/user/blastdb/local")).toBe(true);
  });

  it("species 設定済みは local-only ではない", () => {
    vi.stubEnv("VITE_ENSEMBL_SPECIES", "vicia_faba");
    expect(isLocalOnlyDb("/home/user/blastdb/ref")).toBe(false);
  });
});
