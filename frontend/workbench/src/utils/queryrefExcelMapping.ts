export type queryrefConfidence = "high" | "mid" | "low";

export type queryrefBestGeneMapping = {
  queryGene: string;
  queryChr: string;
  queryStart: number;
  queryEnd: number;
  queryStrand: string;
  v1Gene: string;
  v1Chr: string;
  v1Start: number;
  v1End: number;
  v1Strand: string;
  orientation: string;
  intersection: number;
  minRatio: number;
  jaccard: number;
  candidateCount: number;
  ambiguous: boolean;
  confidence: queryrefConfidence;
  proteinV1?: string;
  proteinScore?: number;
  proteinIdentity?: number;
  proteinCoverage?: number;
  orthologyV1?: string;
  orthologyScore?: number;
  note?: string;
};

export const normalizequeryrefGeneId = (raw: string): string =>
  (raw || "")
    .trim()
    .replace(/^gene:/i, "")
    .replace(/^transcript:/i, "")
    .replace(/-T\d+$/i, "")
    .replace(/\.\d+$/, "");

export const loadqueryrefBestGeneMap = async (): Promise<Map<string, queryrefBestGeneMapping>> =>
  new Map();
