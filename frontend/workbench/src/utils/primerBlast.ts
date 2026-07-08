import type { BlastHit, BlastResponse } from "../types/blast";

export interface PredictedAmplicon {
  dbSource: string;
  subject: string;
  start: number;
  end: number;
  length: number;
  geneIds?: string[];
  geneNames?: string[];
}

export interface PrimerAmpliconSummary {
  amplicons: PredictedAmplicon[];
}

const isLocalHit = (hit: BlastHit): boolean =>
  !!hit.source && hit.source.startsWith("local");

const subjectKey = (hit: BlastHit): string => {
  const chrom = hit.subject_chrom;
  if (chrom) return chrom;
  const firstToken = hit.sseqid.split(/\s+/)[0] || hit.sseqid;
  return firstToken;
};

type Strand = "+" | "-";

interface HitGeom {
  hit: BlastHit;
  dbSource: string;
  subject: string;
  strand: Strand;
  threePrime: number;
  fivePrime: number;
}

const toGeom = (hit: BlastHit): HitGeom => {
  const strand: Strand = hit.sstart <= hit.send ? "+" : "-";
  const s1 = hit.sstart;
  const s2 = hit.send;
  const threePrime = strand === "+" ? Math.max(s1, s2) : Math.min(s1, s2);
  const fivePrime = strand === "+" ? Math.min(s1, s2) : Math.max(s1, s2);
  const dbSource = hit.source || "local";
  const subject = subjectKey(hit);
  return { hit, dbSource, subject, strand, threePrime, fivePrime };
};

/**
 * 左右プライマーの BLAST 結果から、ローカル DB 上で形成しうる PCR 産物候補を列挙する。
 * - local:〜 のヒットのみを対象とする
 * - strand が + / - のヒット同士で、かつ 3' 末端が向かい合う配置のみを採用する
 * - 産物長は 40〜4000bp の範囲に制限する（極端な長さはノイズとして除外）
 */
export const computePrimerAmplicons = (
  left: BlastResponse | null,
  right: BlastResponse | null,
): PrimerAmpliconSummary => {
  if (!left || !right) {
    return { amplicons: [] };
  }

  const leftLocal = left.hits.filter(isLocalHit);
  const rightLocal = right.hits.filter(isLocalHit);
  if (!leftLocal.length || !rightLocal.length) {
    return { amplicons: [] };
  }

  const leftGeom = leftLocal.map(toGeom);
  const rightGeom = rightLocal.map(toGeom);

  const amplicons: PredictedAmplicon[] = [];
  const seen = new Set<string>();

  for (const lg of leftGeom) {
    for (const rg of rightGeom) {
      if (lg.dbSource !== rg.dbSource) continue;
      if (lg.subject !== rg.subject) continue;
      if (lg.strand === rg.strand) continue;
      // 5' 末端の外側から 3' 末端の内側へ向かう配置のみを採用する。
      const start5 = Math.min(lg.fivePrime, rg.fivePrime);
      const end5 = Math.max(lg.fivePrime, rg.fivePrime);
      if (end5 <= start5) continue;

      const innerMin3 = Math.min(lg.threePrime, rg.threePrime);
      const innerMax3 = Math.max(lg.threePrime, rg.threePrime);
      if (!(start5 <= innerMin3 && innerMax3 <= end5)) continue;

      const length = end5 - start5 + 1;
      // ごく短いものだけ除外し、上限は設けない（UI 側のフィルタで制御）。
      if (length < 10) continue;

      const key = `${lg.dbSource}|${lg.subject}|${start5}|${end5}`;
      if (seen.has(key)) continue;
      seen.add(key);

      amplicons.push({
        dbSource: lg.dbSource,
        subject: lg.subject,
        start: start5,
        end: end5,
        length,
      });
    }
  }

  if (!amplicons.length) {
    return { amplicons };
  }

  const allLocalHits = [...leftLocal, ...rightLocal];

  for (const amp of amplicons) {
    const geneIdSet = new Set<string>();
    const geneNameSet = new Set<string>();

    for (const h of allLocalHits) {
      if (subjectKey(h) !== amp.subject) continue;
      const hs = Math.min(h.sstart, h.send);
      const he = Math.max(h.sstart, h.send);
      if (he < amp.start || hs > amp.end) continue;
      (h.gene_ids || []).forEach((gid) => {
        if (gid) geneIdSet.add(gid);
      });
      (h.gene_names || []).forEach((gn) => {
        if (gn) geneNameSet.add(gn);
      });
    }

    if (geneIdSet.size) {
      amp.geneIds = Array.from(geneIdSet);
    }
    if (geneNameSet.size) {
      amp.geneNames = Array.from(geneNameSet);
    }
  }

  return { amplicons };
};

export const countLocalHits = (res: BlastResponse | null): number => {
  if (!res) return 0;
  return res.hits.filter(isLocalHit).length;
};
