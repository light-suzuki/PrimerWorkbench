// Ensembl Plants 関連のユーティリティ

export const inferEnsemblPlantsSpeciesFromGeneId = (geneId: string | undefined | null): string | null => {
  if (!geneId) return null;
  const trimmed = geneId.trim();
  if (!trimmed) return null;

  return (import.meta.env.VITE_ENSEMBL_SPECIES as string | undefined)?.trim() || null;
};

export const inferEnsemblPlantsSpeciesFromDbLabel = (label: string | undefined | null): string | null => {
  if (!label) return null;
  const t = label.trim().toLowerCase();
  if (!t) return null;

  return (import.meta.env.VITE_ENSEMBL_SPECIES as string | undefined)?.trim() || null;
};

export const inferEnsemblPlantsSpecies = (opts: {
  geneId?: string | null;
  dbLabel?: string | null;
}): string | null => {
  return (
    inferEnsemblPlantsSpeciesFromGeneId(opts.geneId) ||
    inferEnsemblPlantsSpeciesFromDbLabel(opts.dbLabel)
  );
};

export const ensemblLocationUrl = (opts: {
  species: string | undefined | null;
  chrom: string | undefined | null;
  start: number | undefined | null;
  end: number | undefined | null;
}): string | null => {
  const species = (opts.species || "").trim();
  const chrom = (opts.chrom || "").trim();
  const start = opts.start;
  const end = opts.end;
  if (!species || !chrom) return null;
  if (start == null || end == null) return null;
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;

  const left = Math.min(start, end);
  const right = Math.max(start, end);
  if (left < 1 || right < 1) return null;

  return `https://plants.ensembl.org/${species}/Location/View?r=${encodeURIComponent(
    `${chrom}:${left}-${right}`,
  )}`;
};

const buildSemicolonQuery = (pairs: Array<[string, string]>): string => {
  return pairs.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join(";");
};

export const inferEnsemblTranscriptId = (opts: {
  geneId?: string | undefined | null;
  geneName?: string | undefined | null;
}): string | null => {
  const geneId = (opts.geneId || "").trim();
  const geneName = (opts.geneName || "").trim();

  const candidates = [geneName, geneId].filter((v) => Boolean(v && v.trim())) as string[];
  for (const c of candidates) {
    if (/-T\\d+$/i.test(c)) return c;
    if (/\\.\\d+$/i.test(c)) return c;
  }

  return null;
};

export const ensemblTranscriptSummaryUrl = (opts: {
  species: string | undefined | null;
  geneId: string | undefined | null;
  transcriptId: string | undefined | null;
  chrom?: string | undefined | null;
  start?: number | undefined | null;
  end?: number | undefined | null;
}): string | null => {
  const species = (opts.species || "").trim();
  const geneId = (opts.geneId || "").trim();
  const transcriptId = (opts.transcriptId || "").trim();
  if (!species || !geneId || !transcriptId) return null;

  const pairs: Array<[string, string]> = [
    ["db", "core"],
    ["g", geneId],
    ["t", transcriptId],
  ];

  const chrom = (opts.chrom || "").trim();
  const start = opts.start;
  const end = opts.end;
  if (chrom && start != null && end != null && Number.isFinite(start) && Number.isFinite(end)) {
    const left = Math.min(start, end);
    const right = Math.max(start, end);
    if (left >= 1 && right >= 1) {
      pairs.push(["r", `${chrom}:${left}-${right}`]);
    }
  }

  return `https://plants.ensembl.org/${species}/Transcript/Summary?${buildSemicolonQuery(pairs)}`;
};

export const ensemblTranscriptExportUrl = (opts: {
  species: string | undefined | null;
  geneId: string | undefined | null;
  transcriptId: string | undefined | null;
  chrom?: string | undefined | null;
  start?: number | undefined | null;
  end?: number | undefined | null;
}): string | null => {
  const species = (opts.species || "").trim();
  const geneId = (opts.geneId || "").trim();
  const transcriptId = (opts.transcriptId || "").trim();
  if (!species || !geneId || !transcriptId) return null;

  const pairs: Array<[string, string]> = [
    ["db", "core"],
    ["flank3_display", "0"],
    ["flank5_display", "0"],
    ["g", geneId],
    ["output", "fasta"],
  ];

  const chrom = (opts.chrom || "").trim();
  const start = opts.start;
  const end = opts.end;
  if (chrom && start != null && end != null && Number.isFinite(start) && Number.isFinite(end)) {
    const left = Math.min(start, end);
    const right = Math.max(start, end);
    if (left >= 1 && right >= 1) {
      pairs.push(["r", `${chrom}:${left}-${right}`]);
    }
  }

  pairs.push(
    ["strand", "feature"],
    ["t", transcriptId],
    ["param", "cdna"],
    ["param", "coding"],
    ["param", "peptide"],
    ["param", "utr5"],
    ["param", "utr3"],
    ["param", "exon"],
    ["param", "intron"],
    ["genomic", "unmasked"],
    ["_format", "Text"],
  );

  return `https://plants.ensembl.org/${species}/Export/Output/Transcript?${buildSemicolonQuery(pairs)}`;
};

/**
 * ローカル BLAST DB で使っている gene ID から、
 * 対応する Ensembl Plants Gene サマリーページの URL を推定する。
 *
 * Species resolution is configured by environment variables.
 *
 * 対応できない ID の場合は null を返す。
 */
export const ensemblGeneUrl = (geneId: string | undefined | null): string | null => {
  if (!geneId) return null;
  const trimmed = geneId.trim();
  if (!trimmed) return null;

  const species = inferEnsemblPlantsSpeciesFromGeneId(trimmed);
  if (species) {
    return `https://plants.ensembl.org/${species}/Gene/Summary?g=${encodeURIComponent(trimmed)}`;
  }

  // 直接対応できない ID は、Ensembl Plants の検索結果ページにフォールバックする。
  // (gene summary が存在しない場合でも、検索なら辿れる可能性がある)
  return `https://plants.ensembl.org/Multi/Search/Results?q=${encodeURIComponent(trimmed)}`;
};

// --- Optional local reference browser integration ---

export const isLocalOnlyDb = (dbLabel: string | undefined | null): boolean => {
  return Boolean(dbLabel?.trim()) && !Boolean((import.meta.env.VITE_ENSEMBL_SPECIES as string | undefined)?.trim());
};

/**
 * ローカル DB のラベルから外部ブラウザ用の species 表示名にマッピングする。
 */
export const getLocalReferenceSpecies = (dbLabel: string | undefined | null): string => {
  void dbLabel;
  return (import.meta.env.VITE_LOCAL_REFERENCE_SPECIES as string | undefined)?.trim() || "";
};

/**
 * ローカル参照ブラウザのベース URL。
 */
export const getLocalReferenceBaseUrl = (): string => {
  return (import.meta.env.VITE_LOCAL_REFERENCE_BASE_URL as string | undefined)?.trim() || "";
};

/**
 * ローカル DB の遺伝子を外部参照ブラウザで開くための URL を生成する。
 */
export const localReferenceGeneUrl = (opts: {
  geneId?: string | null;
  dbLabel?: string | null;
}): string | null => {
  const geneId = (opts.geneId || "").trim();
  if (!geneId) return null;

  const species = getLocalReferenceSpecies(opts.dbLabel);
  const base = getLocalReferenceBaseUrl();
  if (!species || !base) return null;

  return `${base}/${species}/Gene/Summary?g=${encodeURIComponent(geneId)}`;
};

/**
 * ローカル DB の座標を外部参照ブラウザの Location ビューで開くための URL を生成する。
 */
export const localReferenceLocationUrl = (opts: {
  dbLabel?: string | null;
  chrom?: string | null;
  start?: number | null;
  end?: number | null;
}): string | null => {
  const chrom = (opts.chrom || "").trim();
  const start = opts.start;
  const end = opts.end;
  if (!chrom || start == null || end == null) return null;
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;

  const left = Math.min(start, end);
  const right = Math.max(start, end);
  if (left < 1 || right < 1) return null;

  const species = getLocalReferenceSpecies(opts.dbLabel);
  const base = getLocalReferenceBaseUrl();

  return `${base}/${species}/Location/View?r=${encodeURIComponent(`${chrom}:${left}-${right}`)}`;
};

// --- Legacy: UserDBGenomeNavigator 連携 (後方互換性のため残す) ---

/**
 * UserDBGenomeNavigator のベース URL。
 * 環境変数またはデフォルト値を使用。
 */
export const getNavigatorBaseUrl = (): string => {
  // Vite 環境変数または window オブジェクト経由で設定可能
  if (typeof window !== "undefined" && (window as unknown as Record<string, string>).NAVIGATOR_BASE_URL) {
    return (window as unknown as Record<string, string>).NAVIGATOR_BASE_URL;
  }
  // デフォルト: 同一ホストの 8000 ポート
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "http://localhost:8000";
};

/**
 * ローカル DB の遺伝子を UserDBGenomeNavigator で開くための URL を生成する。
 */
export const navigatorGeneUrl = (opts: {
  geneId?: string | null;
  dbLabel?: string | null;
}): string | null => {
  return localReferenceGeneUrl(opts);
};

/**
 * ローカル DB の座標を UserDBGenomeNavigator のゲノムビューで開くための URL を生成する。
 */
export const navigatorLocationUrl = (opts: {
  dbLabel?: string | null;
  chrom?: string | null;
  start?: number | null;
  end?: number | null;
}): string | null => {
  return localReferenceLocationUrl(opts);
};


