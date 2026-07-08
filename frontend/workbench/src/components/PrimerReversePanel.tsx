import React, { useEffect, useMemo, useRef, useState } from "react";
import { bioapiClient } from "../api/bioapiClient";
import type { BlastResponse, BlastHit } from "../types/blast";
import { computePrimerAmplicons, countLocalHits } from "../utils/primerBlast";
import { useWorkbench } from "../utils/workbenchContext";
import { downloadFasta, downloadMarkdown, openPrintViewForMarkdown } from "../utils/exportReport";
import { downloadXlsx } from "../utils/exportXlsx";
import type { XlsxSheet } from "../utils/exportXlsx";
import {
  ensemblGeneUrl,
  ensemblLocationUrl,
  ensemblTranscriptExportUrl,
  ensemblTranscriptSummaryUrl,
  inferEnsemblPlantsSpecies,
  inferEnsemblTranscriptId,
  isLocalOnlyDb,
  navigatorGeneUrl,
  navigatorLocationUrl,
} from "../utils/ensembl";
import {
  DEFAULT_BLAST_DB_BASE,
  query_TO_ref_VIRTUAL_DB_LABEL,
  query_TO_ref_VIRTUAL_DB_VALUE,
  withqueryTorefVirtualDbOption,
  labelForDbPath,
  relabelLocalBlastHits,
  useLocalBlastDbOptions,
  usePreferredLocalDbPaths,
  normalizeLocalDbValue,
} from "../utils/localBlastDbs";
import { useLocalBlastMode } from "../utils/localBlastMode";
import type { JobInfo } from "../types/jobs";
import { useToast } from "./ToastProvider";
import type { BlastLiftoverResult } from "../types/convert";
import { pollJobUntilDone } from "../utils/jobPolling";
import { JobProgressCard } from "./JobProgressCard";
import { extractPrimerSeqsFromLine, normalizePrimerSeq, parseFastaPrimers } from "../utils/primerInput";
import {
  loadqueryrefBestGeneMap,
  normalizequeryrefGeneId,
  type queryrefBestGeneMapping,
} from "../utils/queryrefExcelMapping";

// DB ごとに詳細表示する予測 PCR 産物の上限件数
const TOP_AMPLICONS_PER_DB = 3;

type BulkResultRow = {
  index: number;
  name1?: string;
  name2?: string;
  primer1: string;
  primer2: string;
  primer1Hits: number;
  primer2Hits: number;
  ampliconCount: number;
  quality?: "S" | "A" | "B" | "C" | "D";
  perDb: Array<{
    db: string;
    ampliconCount: number;
    quality?: "S" | "A" | "B" | "C" | "D";
    topAmplicons: Array<{
      subject: string;
      start: number;
      end: number;
      length: number;
      geneLabel?: string;
    }>;
  }>;
};
type BulkResults = BulkResultRow[];

const formatMappedrefRange = (dst: NonNullable<BlastLiftoverResult["dst"]>) => {
  const chromLabel =
    dst.subject_chrom && dst.entry && dst.subject_chrom !== dst.entry
      ? `${dst.subject_chrom} (${dst.entry})`
      : dst.subject_chrom || dst.entry;
  const isMinus = dst.strand === "minus";
  const left = isMinus ? dst.end : dst.start;
  const right = isMinus ? dst.start : dst.end;
  const arrow = isMinus ? "←" : "→";
  const strand = isMinus ? "(-)" : "(+)";
  return `${chromLabel}:${left.toLocaleString()}${arrow}${right.toLocaleString()} ${strand}`;
};

const formatMappedrefRangeFromXlsx = (m: queryrefBestGeneMapping) => {
  const strandRaw = (m.v1Strand || "").trim();
  const isMinus = strandRaw === "-" || /minus/i.test(strandRaw);
  const left = isMinus ? m.v1End : m.v1Start;
  const right = isMinus ? m.v1Start : m.v1End;
  const arrow = isMinus ? "←" : "→";
  const strand = isMinus ? "(-)" : "(+)";
  return `${m.v1Chr}:${left.toLocaleString()}${arrow}${right.toLocaleString()} ${strand}`;
};

const liftoverKey = (srcDb: string, entry: string, start: number, end: number) =>
  `${srcDb}|${entry}|${Math.min(start, end)}|${Math.max(start, end)}`;

type BulkPair = { primer1: string; primer2: string; name1?: string; name2?: string };
type BulkPairsParseResult = { pairs: BulkPair[]; warning: string | null } | { error: string };

const parseBulkPairs = (seqText: string, nameText: string): BulkPairsParseResult => {
  const t = seqText.trim();
  if (!t) return { error: "プライマー配列を入力してください。" };

  let primers: string[] = [];
  let primerNames: (string | undefined)[] = [];

  const hasFastaHeader = t.split(/\r?\n/).some((l) => l.trim().startsWith(">"));
  if (hasFastaHeader) {
    const fasta = parseFastaPrimers(t);
    if (fasta.length) {
      primers = fasta.map((e) => e.seq);
      primerNames = fasta.map((e) => e.name);
    }
  }

  if (!primers.length) {
    const seqs: string[] = [];
    t.split(/\r?\n/).forEach((raw) => {
      extractPrimerSeqsFromLine(raw).forEach((s) => seqs.push(s));
    });
    primers = seqs;
    primerNames = Array.from({ length: primers.length }, () => undefined);
  }

  if (primers.length < 2) {
    return {
      error: "少なくとも 2 本（1 ペア分）のプライマー配列を入力してください。",
    };
  }

  const pairs: BulkPair[] = [];
  for (let i = 0; i + 1 < primers.length; i += 2) {
    pairs.push({ primer1: primers[i], primer2: primers[i + 1] });
  }

  let warning: string | null = null;
  if (primers.length % 2 === 1) {
    warning = "最後の 1 本はペアにできないため無視しました。";
  }

  const usedPrimerCount = pairs.length * 2;
  const usedFastaNames = primerNames.slice(0, usedPrimerCount);
  const hasFastaNames = usedFastaNames.some((n) => Boolean(n && n.trim()));

  const name1List: (string | undefined)[] = [];
  const name2List: (string | undefined)[] = [];

  if (hasFastaNames && usedFastaNames.length >= usedPrimerCount) {
    for (let i = 0; i < pairs.length; i += 1) {
      name1List[i] = usedFastaNames[i * 2];
      name2List[i] = usedFastaNames[i * 2 + 1];
    }
  } else {
    const rawLines = nameText.split(/\r?\n/).map((l) => l.trim());
    while (rawLines.length > 0 && rawLines[rawLines.length - 1] === "") rawLines.pop();
    const hasAnyName = rawLines.some((l) => l.length > 0);

    if (hasAnyName) {
      if (rawLines.length >= usedPrimerCount) {
        // 1 行 = 1 primer（従来互換）
        for (let i = 0; i < pairs.length; i += 1) {
          name1List[i] = rawLines[i * 2] || undefined;
          name2List[i] = rawLines[i * 2 + 1] || undefined;
        }
        if (rawLines.length !== usedPrimerCount) {
          warning = warning
            ? `${warning}（名前行数が一致しないため、先頭 ${usedPrimerCount} 行のみ使用しました）`
            : `名前行数が一致しないため、先頭 ${usedPrimerCount} 行のみ使用しました。`;
        }
      } else if (rawLines.length >= pairs.length) {
        // 1 行 = 1 pair（ペア名、または「name1<TAB>name2」）
        for (let i = 0; i < pairs.length; i += 1) {
          const line = rawLines[i] ?? "";
          const tokens = line.split(/[\t,; ]+/).filter(Boolean);
          if (tokens.length >= 2) {
            name1List[i] = tokens[0];
            name2List[i] = tokens[1];
            continue;
          }
          if (tokens.length === 1) {
            const base = tokens[0];
            name1List[i] = `${base}_P1`;
            name2List[i] = `${base}_P2`;
          }
        }
        if (rawLines.length !== pairs.length) {
          warning = warning
            ? `${warning}（名前は先頭 ${pairs.length} 行のみ使用しました）`
            : `名前は先頭 ${pairs.length} 行のみ使用しました。`;
        }
      } else {
        return {
          error: `名前の行数が足りません（名前: ${rawLines.length} 行, 配列: ${usedPrimerCount} 本 / ${pairs.length} ペア）。名前は空欄にするか、(1) プライマー本数と同じ行数 または (2) ペア数と同じ行数で入力してください。`,
        };
      }
    }
  }

  for (let i = 0; i < pairs.length; i += 1) {
    const baseIndex = i + 1;
    pairs[i].name1 = (name1List[i] || "").trim() || `Pair${baseIndex}_P1`;
    pairs[i].name2 = (name2List[i] || "").trim() || `Pair${baseIndex}_P2`;
  }

  const seen = new Set<string>();
  const dups: string[] = [];
  pairs.forEach((p) => {
    [p.name1, p.name2].forEach((n) => {
      if (!n) return;
      if (seen.has(n)) dups.push(n);
      else seen.add(n);
    });
  });
  if (dups.length > 0) {
    const uniq = Array.from(new Set(dups)).join(", ");
    warning = warning ? `${warning} / 注意: 同じ名前が複数回出現しています: ${uniq}` : `注意: 同じ名前が複数回出現しています: ${uniq}`;
  }

  return { pairs, warning };
};

export const PrimerReversePanel: React.FC = () => {
  const { showToast } = useToast();
  const { options: localDbOptions } = useLocalBlastDbOptions();
  const localDbOptionsWithVirtual = useMemo(
    () => withqueryTorefVirtualDbOption(localDbOptions),
    [localDbOptions],
  );
  const [primer1, setPrimer1] = useState<string>("");
  const [primer2, setPrimer2] = useState<string>("");
  const [mode, setMode] = useState<"bulk" | "single">("bulk");
  const [productMin, setProductMin] = useState<number>(200);
  const [productMax, setProductMax] = useState<number>(1000);

  const [selectedLocalDbs, setSelectedLocalDbs] = usePreferredLocalDbPaths();
  const [customLocalDb, setCustomLocalDb] = useState<string>("");

  type LocalDbView = {
    label: string;
    value: string;
    path: string;
    kind: "db" | "query_to_ref";
  };

  const queryDbPath = useMemo(() => {
    const hit = localDbOptions.find((o) => o.label.toLowerCase() === "UserDB_query");
    return hit?.value || "UserDB_query";
  }, [localDbOptions]);

  const localDbViews = useMemo<LocalDbView[]>(() => {
    const manual = normalizeLocalDbValue(customLocalDb);
    const out: LocalDbView[] = [];
    const seen = new Set<string>();
    const push = (v: LocalDbView) => {
      const key = `${v.kind}|${v.label}|${v.path}`;
      if (seen.has(key)) return;
      seen.add(key);
      out.push(v);
    };

    selectedLocalDbs.forEach((value) => {
      if (value === query_TO_ref_VIRTUAL_DB_VALUE) {
        push({ value, label: query_TO_ref_VIRTUAL_DB_LABEL, path: queryDbPath, kind: "query_to_ref" });
        return;
      }
      push({ value, label: labelForDbPath(value, localDbOptions), path: value, kind: "db" });
    });

    if (manual) {
      push({ value: manual, label: labelForDbPath(manual, localDbOptions), path: manual, kind: "db" });
    }

    return out;
  }, [customLocalDb, localDbOptions, selectedLocalDbs, queryDbPath]);

  const localDbPathsToQuery = useMemo(
    () => Array.from(new Set(localDbViews.map((v) => v.path))).filter(Boolean),
    [localDbViews],
  );

  const wantsqueryTorefView = useMemo(
    () => localDbViews.some((v) => v.kind === "query_to_ref"),
    [localDbViews],
  );
  const [blastMaxHits, setBlastMaxHits] = useState<number>(25);
  const [blastTask, setBlastTask] = useState<string>("blastn-short");
  const [blastEvalue, setBlastEvalue] = useState<number>(10);
  const [blastMaxHsps, setBlastMaxHsps] = useState<number | null>(null);
  const [blastNumThreads, setBlastNumThreads] = useState<number | null>(null);
  const [localMode, setLocalMode] = useLocalBlastMode();

  const [useStandard, setUseStandard] = useState<boolean>(true);
  const [useGpu, setUseGpu] = useState<boolean>(false);
  const [resultTab, setResultTab] = useState<"standard" | "gpu">("standard");
  const blastEngine = "blast";


  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [primer1ResultStandard, setPrimer1ResultStandard] = useState<BlastResponse | null>(null);
  const [primer1ResultGpu, setPrimer1ResultGpu] = useState<BlastResponse | null>(null);
  const [primer2ResultStandard, setPrimer2ResultStandard] = useState<BlastResponse | null>(null);
  const [primer2ResultGpu, setPrimer2ResultGpu] = useState<BlastResponse | null>(null);
  const [bulkNameInput, setBulkNameInput] = useState<string>("");
  const [bulkInput, setBulkInput] = useState<string>("");
  const [bulkLoading, setBulkLoading] = useState<boolean>(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [bulkWarning, setBulkWarning] = useState<string | null>(null);
  const [bulkJobIdStandard, setBulkJobIdStandard] = useState<string | null>(null);
  const [bulkJobIdGpu, setBulkJobIdGpu] = useState<string | null>(null);
  const [bulkJobInfoStandard, setBulkJobInfoStandard] = useState<JobInfo | null>(null);
  const [bulkJobInfoGpu, setBulkJobInfoGpu] = useState<JobInfo | null>(null);
  const [singleJobIdStandard, setSingleJobIdStandard] = useState<string | null>(null);
  const [singleJobIdGpu, setSingleJobIdGpu] = useState<string | null>(null);
  const [singleJobInfoStandard, setSingleJobInfoStandard] = useState<JobInfo | null>(null);
  const [singleJobInfoGpu, setSingleJobInfoGpu] = useState<JobInfo | null>(null);
  const [bulkResultsStandard, setBulkResultsStandard] = useState<BulkResults | null>(null);
  const [bulkResultsGpu, setBulkResultsGpu] = useState<BulkResults | null>(null);
  const [bulkShowAll, setBulkShowAll] = useState<boolean>(false);
  const [queryTorefLoading, setqueryTorefLoading] = useState<boolean>(false);
  const [queryTorefError, setqueryTorefError] = useState<string | null>(null);
  const [queryTorefResults, setqueryTorefResults] = useState<Record<string, BlastLiftoverResult>>({});
  const [queryTorefXlsxLoading, setqueryTorefXlsxLoading] = useState<boolean>(false);
  const [queryTorefXlsxError, setqueryTorefXlsxError] = useState<string | null>(null);
  const [queryTorefXlsxMap, setqueryTorefXlsxMap] = useState<Map<string, queryrefBestGeneMapping> | null>(null);
  const { presetReversePair, setPresetReversePair } = useWorkbench();

  const downloadEnsemblExportFasta = async (opts: {
    speciesPath: string;
    transcriptId: string;
    geneId?: string | null;
    region?: string | null;
    fileBase?: string;
  }) => {
    const speciesPath = (opts.speciesPath || "").trim();
    const transcriptId = (opts.transcriptId || "").trim();
    if (!speciesPath || !transcriptId) return;
    try {
      const fasta = await bioapiClient.fetchEnsemblTranscriptExportFasta({
        species_path: speciesPath,
        transcript_id: transcriptId,
        gene_id: opts.geneId?.trim() || undefined,
        region: opts.region?.trim() || undefined,
      });
      downloadFasta(fasta, opts.fileBase || `ensembl_${transcriptId}`);
      showToast("FASTA を保存しました", "success");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "FASTA 取得に失敗しました。";
      showToast(msg, "error");
    }
  };

  const STORAGE_KEY = "seq_workbench_primer_reverse";

  const refDbPath = "UserDB_ref";

  const localDbLabelToPath = useMemo(() => {
    const map = new Map<string, string>();
    localDbViews.forEach((view) => {
      const base = view.path.split(/[/\\]/).filter(Boolean).pop() ?? view.path;
      map.set(base, view.path);
      map.set(view.label, view.path);
    });
    return map;
  }, [localDbViews]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const isReload = (() => {
        try {
          const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
          if (nav && nav.type) return nav.type === "reload";
          const legacy = (performance as any).navigation;
          return legacy && legacy.type === 1;
        } catch {
          return false;
        }
      })();
      if (isReload) {
        window.localStorage.removeItem(STORAGE_KEY);
        return;
      }
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw) as Partial<{
        primer1: string;
        primer2: string;
        productMin: number;
        productMax: number;
        customLocalDb: string;
        blastTask: string;
        blastEvalue: number;
      }>;
      if (saved.primer1) setPrimer1(saved.primer1);
      if (saved.primer2) setPrimer2(saved.primer2);
      if (typeof saved.productMin === "number") setProductMin(saved.productMin);
      if (typeof saved.productMax === "number") setProductMax(saved.productMax);
      if (typeof saved.customLocalDb === "string") setCustomLocalDb(saved.customLocalDb);
      if (typeof saved.blastTask === "string") setBlastTask(saved.blastTask);
      if (typeof saved.blastEvalue === "number") setBlastEvalue(saved.blastEvalue);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const payload = {
      primer1,
      primer2,
      productMin,
      productMax,
      customLocalDb,
      blastTask,
      blastEvalue,
    };
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {
      // ignore
    }
  }, [
    primer1,
    primer2,
    productMin,
    productMax,
    customLocalDb,
    blastTask,
    blastEvalue,
  ]);

  // 外部タブからのプリセットを受け取る
  useEffect(() => {
    if (presetReversePair && presetReversePair.primer1 && presetReversePair.primer2) {
      setPrimer1(presetReversePair.primer1);
      setPrimer2(presetReversePair.primer2);
      setMode("single");
      if (setPresetReversePair) {
        setPresetReversePair(null);
      }
    }
  }, [presetReversePair, setPresetReversePair]);

  const toggleLocalDb = (path: string) => {
    setSelectedLocalDbs((prev) =>
      prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path],
    );
  };

  const labelForDb = (path: string) =>
    labelForDbPath(path, localDbOptions);


  const primer1Result = resultTab === "standard" ? primer1ResultStandard : primer1ResultGpu;
  const primer2Result = resultTab === "standard" ? primer2ResultStandard : primer2ResultGpu;
  const bulkResults = resultTab === "standard" ? bulkResultsStandard : bulkResultsGpu;
  const bulkJobId = resultTab === "standard" ? bulkJobIdStandard : bulkJobIdGpu;
  const bulkJobInfo = resultTab === "standard" ? bulkJobInfoStandard : bulkJobInfoGpu;

  const handleSearch = async () => {
    const left = primer1.trim();
    const right = primer2.trim();
    if (!left || !right) {
      setError("2 本のプライマー配列（5'→3'）を入力してください。");
      return;
    }
    if (localDbPathsToQuery.length === 0) {
      setError("ローカル BLAST+ を使うために、少なくとも 1 つ BLAST DB を選択してください。");
      return;
    }
    setLoading(true);
    setError(null);

    setPrimer1ResultStandard(null);
    setPrimer1ResultGpu(null);
    setPrimer2ResultStandard(null);
    setPrimer2ResultGpu(null);
    setSingleJobIdStandard(null);
    setSingleJobIdGpu(null);
    setSingleJobInfoStandard(null);
    setSingleJobInfoGpu(null);
    setqueryTorefResults({});
    setqueryTorefError(null);
    try {
      const promises: Promise<void>[] = [];

      const normalizedLeft = normalizePrimerSeq(left);
      const normalizedRight = normalizePrimerSeq(right);
      if (!normalizedLeft || !normalizedRight) {
        throw new Error("プライマー配列を正規化できませんでした（文字種を確認してください）。");
      }

      const runJob = async (
        engine: "blast" | "cuda",
        setJobId: (id: string | null) => void,
        setJobInfo: (info: JobInfo | null) => void,
        setRes1: (res: BlastResponse | null) => void,
        setRes2: (res: BlastResponse | null) => void,
      ) => {
        const job = await bioapiClient.createBlastBatchLocalJob({
          sequences: [normalizedLeft, normalizedRight],
          dbs: localDbPathsToQuery,
          local_mode: localMode,
          task: blastTask,
          evalue: blastEvalue,
          max_target_seqs: blastMaxHits,
          max_hsps: blastMaxHsps ?? undefined,
          num_threads: blastNumThreads ?? undefined,
          engine,
        });
        setJobId(job.job_id);

        const info = await pollJobUntilDone(job.job_id, {
          onUpdate: (i) => setJobInfo(i),
          intervalMs: 900,
        });
        if (info.status !== "succeeded") {
          throw new Error(info.error ?? "BLAST ジョブに失敗しました。");
        }

        const batch = await bioapiClient.getJobResult<{ results: BlastResponse[] }>(job.job_id);
        const res1Raw = batch.results?.[0] ?? null;
        const res2Raw = batch.results?.[1] ?? null;
        if (!res1Raw || !res2Raw) {
          throw new Error("BLAST の結果を取得できませんでした。");
        }

        const hits1 = relabelLocalBlastHits(res1Raw.hits ?? [], localDbPathsToQuery, localDbOptions);
        const hits2 = relabelLocalBlastHits(res2Raw.hits ?? [], localDbPathsToQuery, localDbOptions);
        setRes1({ num_hits: hits1.length, hits: hits1 });
        setRes2({ num_hits: hits2.length, hits: hits2 });
      };

      if (useStandard) {
        promises.push(
          runJob(
            "blast",
            setSingleJobIdStandard,
            setSingleJobInfoStandard,
            setPrimer1ResultStandard,
            setPrimer2ResultStandard,
          ),
        );
      } else {
        setPrimer1ResultStandard(null);
        setPrimer2ResultStandard(null);
      }

      if (useGpu) {
        promises.push(
          runJob(
            "cuda",
            setSingleJobIdGpu,
            setSingleJobInfoGpu,
            setPrimer1ResultGpu,
            setPrimer2ResultGpu,
          ),
        );
      } else {
        setPrimer1ResultGpu(null);
        setPrimer2ResultGpu(null);
      }

      if (promises.length === 0) {
        throw new Error("実行エンジン（Standard または GPU）を少なくとも 1 つ選択してください。");
      }

      await Promise.all(promises);

      if (useStandard && !useGpu) setResultTab("standard");
      if (!useStandard && useGpu) setResultTab("gpu");

    } catch (e) {
      const msg =
        e instanceof Error
          ? e.message
          : "プライマー逆引き BLAST 中に思わぬエラーが発生しました。";
      setError(msg);
    } finally {
      setLoading(false);
      setSingleJobIdStandard(null);
      setSingleJobIdGpu(null);
      setSingleJobInfoStandard(null);
      setSingleJobInfoGpu(null);
    }
  };

  const amplicons = useMemo(() => {
    const { amplicons } = computePrimerAmplicons(primer1Result, primer2Result);
    if (!amplicons.length) return [];
    const filteredRaw = amplicons.filter(
      (a) => a.length >= productMin && a.length <= productMax,
    );
    const viewLabels = new Set(localDbViews.map((v) => v.label));
    const queryLabel = labelForDbPath(queryDbPath, localDbOptions);
    const out: typeof filteredRaw = [];
    for (const a of filteredRaw) {
      const tag = (a.dbSource || "").replace(/^local:/, "");
      if (viewLabels.has(tag)) out.push(a);
      if (wantsqueryTorefView && tag === queryLabel) {
        out.push({ ...a, dbSource: `local:${query_TO_ref_VIRTUAL_DB_LABEL}` });
      }
    }
    return out;
  }, [
    localDbOptions,
    localDbViews,
    primer1Result,
    primer2Result,
    productMax,
    productMin,
    queryDbPath,
    wantsqueryTorefView,
  ]);

  const needsqueryTorefXlsxMap = useMemo(() => {
    if (!wantsqueryTorefView) return false;
    const isqueryGene = (g?: string | null) => !!g && /^GENE\\.reference\\.query\\./i.test(g);
    const hasInAmplicons = amplicons.some((a) => {
      const genes = [
        ...(a.geneNames ?? []),
        ...(a.geneIds ?? []),
      ].filter(Boolean);
      return genes.some((g) => isqueryGene(g));
    });
    if (hasInAmplicons) return true;
    const bulk = bulkResults ?? [];
    return bulk.some((r) =>
      (r.perDb ?? []).some((d) => /query/i.test(d.db) && (d.topAmplicons ?? []).some((a) => isqueryGene(a.geneLabel))),
    );
  }, [amplicons, bulkResults, wantsqueryTorefView]);

  useEffect(() => {
    if (!needsqueryTorefXlsxMap) return;
    if (queryTorefXlsxLoading) return;
    if (queryTorefXlsxMap) return;

    let cancelled = false;
    setqueryTorefXlsxLoading(true);
    setqueryTorefXlsxError(null);
    loadqueryrefBestGeneMap()
      .then((m) => {
        if (cancelled) return;
        setqueryTorefXlsxMap(m);
      })
      .catch((e) => {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : "query→ref 対応表（Excel）の読み込みに失敗しました。";
        setqueryTorefXlsxError(msg);
      })
      .finally(() => {
        if (cancelled) return;
        setqueryTorefXlsxLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [needsqueryTorefXlsxMap, queryTorefXlsxLoading, queryTorefXlsxMap]);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (!wantsqueryTorefView) return;
      if (queryTorefLoading) return;

      const groups = new Map<string, Array<{ entry: string; start: number; end: number }>>();
      const seen = new Set<string>();

      const addRegion = (dbLabel: string, entry: string, start: number, end: number) => {
        const srcDb = localDbLabelToPath.get(dbLabel);
        if (!srcDb) return;
        const key = liftoverKey(srcDb, entry, start, end);
        if (queryTorefResults[key]) return;
        if (seen.has(key)) return;
        seen.add(key);
        const arr = groups.get(srcDb) ?? [];
        arr.push({ entry, start, end });
        groups.set(srcDb, arr);
      };

      // single pair table
      amplicons.forEach((a) => {
        const dbLabel = (a.dbSource || "").replace(/^local:/, "");
        if (!/query/i.test(dbLabel)) return;
        addRegion(dbLabel, a.subject, a.start, a.end);
      });

      // bulk table (topAmplicons only)
      (bulkResults ?? []).forEach((r) => {
        (r.perDb ?? []).forEach((d) => {
          if (!/query/i.test(d.db)) return;
          (d.topAmplicons ?? []).forEach((a) => {
            addRegion(d.db, a.subject, a.start, a.end);
          });
        });
      });

      if (groups.size === 0) return;

      setqueryTorefLoading(true);
      setqueryTorefError(null);
      try {
        const calls = Array.from(groups.entries()).map(async ([srcDb, regions]) => {
          const res = await bioapiClient.liftoverBlast({
            src_db: srcDb,
            dst_db: refDbPath,
            regions: regions.slice(0, 250),
            task: "megablast",
            evalue: 1e-20,
            max_target_seqs: 5,
            max_hsps: 1,
            num_threads: blastNumThreads ?? undefined,
            max_len: 50_000,
            padding_bp: 0,
          });
          return { srcDb, res };
        });

        const settled = await Promise.all(calls);
        if (cancelled) return;
        setqueryTorefResults((prev) => {
          const next = { ...prev };
          settled.forEach(({ srcDb, res }) => {
            (res.results ?? []).forEach((r) => {
              const key = liftoverKey(srcDb, r.src_entry, r.src_start, r.src_end);
              next[key] = r;
            });
          });
          return next;
        });
      } catch (e) {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : "query→ref 変換（BLAST）に失敗しました。";
        setqueryTorefError(msg);
      } finally {
        if (!cancelled) setqueryTorefLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [
    amplicons,
    blastNumThreads,
    bulkResults,
    localDbLabelToPath,
    refDbPath,
    queryTorefLoading,
    queryTorefResults,
    wantsqueryTorefView,
  ]);

  const handleBulkSearch = async () => {
    const blastEngine = "blast";
    if (bulkLoading || bulkJobId) return;
    setBulkWarning(null);
    const parsed = parseBulkPairs(bulkInput, bulkNameInput);
    if ("error" in parsed) {
      setBulkError(parsed.error);
      (blastEngine === "blast" ? setBulkResultsStandard : setBulkResultsGpu)(null);
      return;
    }
    const pairs = parsed.pairs;
    setBulkWarning(parsed.warning);
    if (localDbPathsToQuery.length === 0) {
      setBulkError("ローカル BLAST DB を少なくとも 1 つ選択してください。");
      (blastEngine === "blast" ? setBulkResultsStandard : setBulkResultsGpu)(null);
      return;
    }
    if (pairs.length === 0) {
      setBulkError("有効なペアを構成できませんでした（行数が奇数になっていませんか？）。");
      (blastEngine === "blast" ? setBulkResultsStandard : setBulkResultsGpu)(null);
      return;
    }

    setBulkLoading(true);
    setBulkError(null);
    setqueryTorefResults({});
    setqueryTorefError(null);
    (blastEngine === "blast" ? setBulkResultsStandard : setBulkResultsGpu)(null);
    setBulkShowAll(false);
    (blastEngine === "blast" ? setBulkJobInfoStandard : setBulkJobInfoGpu)(null);
    try {
      // まとめて /blast/run_batch_local に投げて、高速にローカル BLAST を実行する。
      const normalizedSeqs: string[] = [];
      pairs.forEach((p) => {
        normalizedSeqs.push(normalizePrimerSeq(p.primer1));
        normalizedSeqs.push(normalizePrimerSeq(p.primer2));
      });

      const job = await bioapiClient.createBlastBatchLocalJob({
        sequences: normalizedSeqs,
        dbs: localDbPathsToQuery,
        local_mode: localMode,
        task: blastTask,
        evalue: blastEvalue,
        max_target_seqs: blastMaxHits,
        max_hsps: blastMaxHsps ?? undefined,
        num_threads: blastNumThreads ?? undefined,
        engine: blastEngine,
      });
      (blastEngine === "blast" ? setBulkJobIdStandard : setBulkJobIdGpu)(job.job_id);
      showToast("一括 BLAST ジョブを開始しました", "info");

      const info = await pollJobUntilDone(job.job_id, {
        onUpdate: (i) => (blastEngine === "blast" ? setBulkJobInfoStandard : setBulkJobInfoGpu)(i),
        intervalMs: 900,
      });
      if (info.status !== "succeeded") {
        throw new Error(info.error ?? "一括 BLAST ジョブに失敗しました。");
      }

      const batch = await bioapiClient.getJobResult<{ results: BlastResponse[] }>(job.job_id);

      if (!batch.results || batch.results.length !== normalizedSeqs.length) {
        throw new Error("run_batch_local の結果件数が期待値と一致しませんでした。");
      }

      const relabeledBatchResults = (batch.results ?? []).map((r) => ({
        ...r,
        hits: relabelLocalBlastHits(r.hits ?? [], localDbPathsToQuery, localDbOptions),
      }));

      const queryLabel = labelForDbPath(queryDbPath, localDbOptions);
      const viewLabels = new Set(localDbViews.map((v) => v.label));

      const allResults: Array<{
        index: number;
        name1?: string;
        name2?: string;
        primer1: string;
        primer2: string;
        primer1Hits: number;
        primer2Hits: number;
        ampliconCount: number;
        quality?: "S" | "A" | "B" | "C" | "D";
        perDb: Array<{
          db: string;
          ampliconCount: number;
          quality?: "S" | "A" | "B" | "C" | "D";
          topAmplicons: {
            subject: string;
            start: number;
            end: number;
            length: number;
            geneLabel?: string;
          }[];
        }>;
      }> = [];

      for (let i = 0; i < pairs.length; i += 1) {
        const pair = pairs[i];
        const res1 = relabeledBatchResults[i * 2] ?? null;
        const res2 = relabeledBatchResults[i * 2 + 1] ?? null;
        const { amplicons } = computePrimerAmplicons(res1, res2);
        const ampFilteredRaw = amplicons.filter(
          (a) => a.length >= productMin && a.length <= productMax,
        );
        const ampFiltered = (() => {
          const out: typeof ampFilteredRaw = [];
          for (const a of ampFilteredRaw) {
            const tag = (a.dbSource || "").replace(/^local:/, "");
            if (viewLabels.has(tag)) out.push(a);
            if (wantsqueryTorefView && tag === queryLabel) {
              out.push({ ...a, dbSource: `local:${query_TO_ref_VIRTUAL_DB_LABEL}` });
            }
          }
          return out;
        })();
        const perDbMap = new Map<
          string,
          { count: number; best?: (typeof ampFiltered)[number] }
        >();
        for (const a of ampFiltered) {
          const key = a.dbSource;
          const rec = perDbMap.get(key) ?? { count: 0, best: undefined };
          rec.count += 1;
          if (!rec.best || (a.length ?? 0) < (rec.best.length ?? 0)) {
            rec.best = a;
          }
          perDbMap.set(key, rec);
        }

        const perDb: Array<{
          db: string;
          ampliconCount: number;
          quality?: "S" | "A" | "B" | "C" | "D";
          topAmplicons: {
            subject: string;
            start: number;
            end: number;
            length: number;
            geneLabel?: string;
          }[];
        }> = [];

        const pathToLabel = (dbSource: string): string => {
          const plain = dbSource.replace(/^local:/, "");
          return plain;
        };

        const gradeFromAmp = (n: number): "S" | "A" | "B" | "C" | "D" => {
          if (n === 1) return "S";
          if (n === 0) return "D";
          if (n === 2) return "C";
          if (n === 3) return "B";
          return "D";
        };

        for (const [dbSrc, rec] of perDbMap.entries()) {
          const allForDb = ampFiltered.filter((a) => a.dbSource === dbSrc);
          const topList = allForDb
            .slice()
            .sort((a, b) => (a.length ?? 0) - (b.length ?? 0))
            .slice(0, TOP_AMPLICONS_PER_DB);
          const topAmplicons =
            topList.map((a) => {
              let geneLabel: string | undefined;
              if (a.geneNames && a.geneNames.length > 0) {
                geneLabel = a.geneNames[0];
              } else if (a.geneIds && a.geneIds.length > 0) {
                geneLabel = a.geneIds[0];
              }
              return {
                subject: a.subject,
                start: a.start,
                end: a.end,
                length: a.length,
                geneLabel,
              };
            }) ?? [];

          perDb.push({
            db: pathToLabel(dbSrc),
            ampliconCount: rec.count,
            quality: gradeFromAmp(rec.count),
            topAmplicons,
          });
        }

        // 選択中のローカル DB について、amplicon が 0 のものも明示的に加える
        localDbViews.forEach((view) => {
          const label = view.label;
          if (!perDb.some((d) => d.db === label)) {
            perDb.push({
              db: label,
              ampliconCount: 0,
              quality: gradeFromAmp(0),
              topAmplicons: [],
            });
          }
        });

        // 総合品質: DB ごとの品質の中で最も悪いものを採用
        const qualityOrder: Record<"S" | "A" | "B" | "C" | "D", number> = {
          S: 1,
          A: 2,
          B: 3,
          C: 4,
          D: 5,
        };
        let quality: "S" | "A" | "B" | "C" | "D" = "D";
        if (perDb.length > 0) {
          const letters = perDb
            .map((d) => d.quality)
            .filter((q): q is "S" | "A" | "B" | "C" | "D" => !!q);
          if (letters.length > 0) {
            quality = letters.reduce((worst, q) =>
              qualityOrder[q] > qualityOrder[worst] ? q : worst,
            );
          }
        }

        allResults.push({
          index: i + 1,
          name1: pair.name1,
          name2: pair.name2,
          primer1: pair.primer1,
          primer2: pair.primer2,
          primer1Hits: countLocalHits(res1),
          primer2Hits: countLocalHits(res2),
          ampliconCount: ampFiltered.length,
          quality,
          perDb,
        });
      }

      (blastEngine === "blast" ? setBulkResultsStandard : setBulkResultsGpu)(allResults);
      showToast("一括評価が完了しました", "success");
    } catch (e) {
      const msg =
        e instanceof Error
          ? e.message
          : "複数プライマーの逆引き BLAST 中に思わぬエラーが発生しました。";
      setBulkError(msg);
      showToast(msg, "error");
    } finally {
      setBulkLoading(false);
      (blastEngine === "blast" ? setBulkJobIdStandard : setBulkJobIdGpu)(null);
      (blastEngine === "blast" ? setBulkJobInfoStandard : setBulkJobInfoGpu)(null);
    }
  };

  const cancelBulkJob = async () => {
    if (!bulkJobId) return;
    try {
      await bioapiClient.cancelJob(bulkJobId);
      showToast("キャンセルを要求しました（実行中の BLAST はすぐ止まらないことがあります）", "info");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "キャンセルに失敗しました。";
      showToast(msg, "error");
    }
  };

  const cancelSingleJobStandard = async () => {
    if (!singleJobIdStandard) return;
    try {
      await bioapiClient.cancelJob(singleJobIdStandard);
      showToast("キャンセルを要求しました（実行中の BLAST はすぐ止まらないことがあります）", "info");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "キャンセルに失敗しました。";
      showToast(msg, "error");
    }
  };

  const cancelSingleJobGpu = async () => {
    if (!singleJobIdGpu) return;
    try {
      await bioapiClient.cancelJob(singleJobIdGpu);
      showToast("キャンセルを要求しました（実行中の BLAST はすぐ止まらないことがあります）", "info");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "キャンセルに失敗しました。";
      showToast(msg, "error");
    }
  };

  const renderSummaryRow = () => {
    if (!primer1Result || !primer2Result) return null;
    return (
      <p className="seq-hint">
        BLAST ヒット（ローカル合算）: Primer1 {primer1Result.num_hits} 件 / Primer2{" "}
        {primer2Result.num_hits} 件 ／ 予測 PCR 産物（長さ {productMin}–{productMax} bp）:{" "}
        {amplicons.length} 本
      </p>
    );
  };

  const buildMarkdownReport = (): string => {
    const dt = new Date();
    const lines: string[] = [];
    lines.push("# プライマー逆引きレポート");
    lines.push("");
    lines.push(`- 作成時刻: ${dt.toLocaleString()}`);
    lines.push(
      `- 使用 DB: ${localDbViews.length ? localDbViews.map((v) => v.label).join(", ") : "(未選択)"
      }`,
    );
    lines.push(`- 産物長フィルタ: \`${productMin}-${productMax} bp\``);
    lines.push("");

    if (primer1 && primer2) {
      lines.push("## 単一ペアの結果");
      lines.push("");
      lines.push(
        `- Primer1: \`${primer1.trim()}\` ／ Primer2: \`${primer2.trim()}\``,
      );
      if (primer1Result || primer2Result) {
        const { amplicons } = computePrimerAmplicons(
          primer1Result,
          primer2Result,
        );
        lines.push(
          `- BLAST ヒット数: Primer1=${primer1Result?.num_hits ?? 0}, Primer2=${primer2Result?.num_hits ?? 0
          }`,
        );
        lines.push(
          `- 予測 PCR 産物数 (${productMin}–${productMax} bp): ${amplicons.length} 本`,
        );
      }
      lines.push("");
    }

    if (bulkResults && bulkResults.length > 0) {
      lines.push("## 一括評価結果");
      lines.push("");
      lines.push(
        "| # | Name1 | Name2 | Primer1 | Primer2 | 品質 (総合) |",
      );
      lines.push("| ---: | --- | --- | --- | --- | :--: |");
      bulkResults.forEach((r) => {
        lines.push(
          `| ${r.index} | \`${r.name1 ?? ""}\` | \`${r.name2 ?? ""}\` | \`${r.primer1
          }\` | \`${r.primer2}\` | ${r.quality ?? ""} |`,
        );
      });
      lines.push("");
    }

    if (bulkResults && bulkResults.length > 0) {
      lines.push("### DB 別の代表的な産物");
      lines.push("");
      lines.push(
        "| # | DB | 予測産物数 | 代表産物 (subject:start-end, 長さbp, gene) | 品質 |",
      );
      lines.push("| ---: | --- | ---: | --- | :--: |");
      bulkResults.forEach((r) => {
        r.perDb.forEach((d) => {
          const top = d.topAmplicons[0];
          let desc = "-";
          if (top) {
            const coordLabel = `${top.subject}:${top.start}-${top.end} (${top.length}bp)`;
            const species = inferEnsemblPlantsSpecies({ geneId: top.geneLabel, dbLabel: d.db });
            const locUrl = ensemblLocationUrl({
              species,
              chrom: top.subject,
              start: top.start,
              end: top.end,
            });
            const coord = locUrl ? `[${coordLabel}](${locUrl})` : coordLabel;
            if (top.geneLabel) {
              const url = ensemblGeneUrl(top.geneLabel);
              const genePart = url
                ? `[${top.geneLabel}](${url})`
                : top.geneLabel;
              desc = `${coord}, ${genePart}`;
            } else {
              desc = coord;
            }
          }
          lines.push(
            `| ${r.index} | ${d.db} | ${d.ampliconCount} | ${desc} | ${d.quality ?? ""
            } |`,
          );
        });
      });
      lines.push("");
    }

    return lines.join("\n");
  };

  const buildXlsxSheets = (): XlsxSheet[] => {
    const sheets: XlsxSheet[] = [];

    const summary: Array<Array<string | number | null>> = [
      ["created", new Date().toLocaleString()],
      ["product_min", productMin],
      ["product_max", productMax],
      ["blast_task", blastTask],
      ["blast_evalue", blastEvalue],
      ["dbs", localDbViews.map((v) => v.label).join(", ")],
    ];

    if (primer1 && primer2) {
      summary.push(["single_primer1", primer1.trim()]);
      summary.push(["single_primer2", primer2.trim()]);
      summary.push(["single_hits_primer1", primer1Result?.num_hits ?? null]);
      summary.push(["single_hits_primer2", primer2Result?.num_hits ?? null]);
      summary.push(["single_amplicons_filtered", amplicons.length]);
    }

    if (bulkResults?.length) {
      summary.push(["bulk_pairs", bulkResults.length]);
    }

    sheets.push({ name: "Summary", data: summary });

    if (amplicons.length > 0) {
      const header = ["#", "db", "subject", "start", "end", "length_bp", "gene"];
      const rows = amplicons.map((a, idx) => {
        const allGenes = [...(a.geneNames ?? []), ...(a.geneIds ?? [])].filter(Boolean);
        const gene = Array.from(new Set(allGenes)).join("|");
        return [
          idx + 1,
          a.dbSource,
          a.subject,
          a.start,
          a.end,
          a.length,
          gene || "",
        ];
      });
      sheets.push({ name: "Amplicons", data: [header, ...rows] });
    }

    if (bulkResults && bulkResults.length > 0) {
      const orderedDbs = (() => {
        const labels = localDbViews.map((v) => v.label);
        const uniq: string[] = [];
        const seen = new Set<string>();
        labels.forEach((l) => {
          if (!l) return;
          if (seen.has(l)) return;
          seen.add(l);
          uniq.push(l);
        });
        bulkResults
          .flatMap((r) => r.perDb.map((d) => d.db))
          .forEach((db) => {
            if (seen.has(db)) return;
            seen.add(db);
            uniq.push(db);
          });
        return uniq;
      })();

      const header: string[] = [
        "#",
        "name1",
        "name2",
        "primer1",
        "primer2",
        "primer1_hits",
        "primer2_hits",
        "amplicons_total",
        "quality_total",
        ...orderedDbs.flatMap((db) => [
          `${db}_amplicons`,
          `${db}_quality`,
          `${db}_top`,
        ]),
      ];

      const rows = bulkResults.map((r) => {
        const map = new Map(r.perDb.map((d) => [d.db, d]));
        const row: Array<string | number | null> = [
          r.index,
          r.name1 ?? "",
          r.name2 ?? "",
          r.primer1,
          r.primer2,
          r.primer1Hits,
          r.primer2Hits,
          r.ampliconCount,
          r.quality ?? "",
        ];
        orderedDbs.forEach((db) => {
          const d = map.get(db);
          const top = d?.topAmplicons?.[0];
          const topText = top
            ? `${top.subject}:${top.start}-${top.end} (${top.length}bp)${top.geneLabel ? `, ${top.geneLabel}` : ""}`
            : "";
          row.push(d?.ampliconCount ?? 0);
          row.push(d?.quality ?? "");
          row.push(topText);
        });
        return row;
      });

      sheets.push({ name: "BulkSummary", data: [header, ...rows] });

      const perDbHeader = [
        "pair_index",
        "db",
        "amplicons",
        "quality",
        "rank",
        "subject",
        "start",
        "end",
        "length_bp",
        "gene",
      ];
      const perDbRows: Array<Array<string | number | null>> = [];
      bulkResults.forEach((r) => {
        r.perDb.forEach((d) => {
          if (!d.topAmplicons.length) {
            perDbRows.push([r.index, d.db, d.ampliconCount, d.quality ?? "", null, "", null, null, null, ""]);
            return;
          }
          d.topAmplicons.forEach((a, idx) => {
            perDbRows.push([
              r.index,
              d.db,
              d.ampliconCount,
              d.quality ?? "",
              idx + 1,
              a.subject,
              a.start,
              a.end,
              a.length,
              a.geneLabel ?? "",
            ]);
          });
        });
      });
      sheets.push({ name: "BulkPerDb", data: [perDbHeader, ...perDbRows] });
    }

    return sheets;
  };

  const bulkDbLabels = useMemo(() => {
    const out: string[] = [];
    const seen = new Set<string>();
    localDbViews.forEach((v) => {
      const label = (v.label || "").trim();
      if (!label) return;
      if (seen.has(label)) return;
      seen.add(label);
      out.push(label);
    });
    (bulkResults ?? []).forEach((r) => {
      (r.perDb ?? []).forEach((d) => {
        const label = (d.db || "").trim();
        if (!label) return;
        if (seen.has(label)) return;
        seen.add(label);
        out.push(label);
      });
    });
    return out;
  }, [bulkResults, localDbViews]);

  const busy = loading || bulkLoading;

  return (
    <section className="seq-result-block">
      <h2 className="panel-title">プライマー逆引き（ローカル BLAST）</h2>
      <p className="panel-hint">
        既存のプライマーペア（5&apos;→3&apos;）を入力し、向き（F/R）は気にせずローカル BLAST DB に対して BLAST して予測
        PCR 産物の位置（染色体 / 座標 / 長さ）と gene を一覧表示します。下部のテキストエリアには Excel から
        「名前列」と「配列列」をそのまま貼り付けて、一括評価できます（1行=1ペア、2行=1ペア、FASTA も対応）。
      </p>
      <details className="ui-details" style={{ marginBottom: "0.4rem" }}>
        <summary>エクスポート</summary>
        <div className="ui-details-body">
          <div className="primer-row">
            <button
              type="button"
              className="seq-button secondary"
              onClick={() => {
                downloadXlsx(buildXlsxSheets(), "primer_reverse");
              }}
              disabled={!primer1Result && !primer2Result && !bulkResults}
            >
              Excel (.xlsx)
            </button>
            <button
              type="button"
              className="seq-button secondary"
              onClick={() => {
                const md = buildMarkdownReport();
                if (!md) return;
                downloadMarkdown(md, "primer_reverse");
              }}
              disabled={!primer1Result && !primer2Result && !bulkResults}
            >
              Markdown
            </button>
            <button
              type="button"
              className="seq-button secondary"
              onClick={() => {
                const md = buildMarkdownReport();
                if (!md) return;
                openPrintViewForMarkdown(md, "プライマー逆引きレポート");
              }}
              disabled={!primer1Result && !primer2Result && !bulkResults}
            >
              印刷（PDF）
            </button>
          </div>
        </div>
      </details>

      <div className="primer-grid">
        <div className="primer-controls">
          <div
            className="primer-tabs"
            style={
              {
                ["--seg-accent" as any]: "var(--accent-2)",
              } as React.CSSProperties
            }
          >
            <button
              type="button"
              className={`primer-tab-btn ${mode === "bulk" ? "is-active" : ""}`}
              onClick={() => setMode("bulk")}
            >
              一括（メイン）
            </button>
            <button
              type="button"
              className={`primer-tab-btn ${mode === "single" ? "is-active" : ""}`}
              onClick={() => setMode("single")}
            >
              単一ペア（詳細）
            </button>
          </div>

          {mode === "bulk" ? (
            <>
              <p className="panel-hint">
                Excel などからプライマー配列を貼り付けて、一括でローカル BLAST による逆引きと品質評価を行います（1行=1ペア / 2行=1ペア / FASTA 可）。
              </p>

              <label className="seq-label">
                プライマー配列（5&apos;→3&apos;、2行=1ペア / 1行=1ペア / FASTA 可）:
                <textarea
                  className="seq-textarea"
                  rows={8}
                  placeholder={"例:\nGCATGGCTTGTGATGCAACA\nTGGTACGTGTGGTTCAGTTTCA\nTCCACCACATTTTTCAGCTTTT\nAAGGAAAGCTGTCAAGGCAC"}
                  value={bulkInput}
                  onChange={(e) => setBulkInput(e.target.value)}
                />
              </label>

              <details className="ui-details">
                <summary>名前（任意）</summary>
                <div className="ui-details-body">
                  <label className="seq-label">
                    名前（2行=1ペア または 1行=1ペア）:
                    <textarea
                      className="seq-textarea"
                      rows={6}
                      placeholder={"例:\nChr1_2918_XholI_Caps_Fw\nChr1_2918_XholI_Caps_Re\nChr1_2933_XholI_Caps_Fw\nChr1_2933_XholI_Caps_Re"}
                      value={bulkNameInput}
                      onChange={(e) => setBulkNameInput(e.target.value)}
                    />
                  </label>
                </div>
              </details>

              <div className="primer-row">
                <button
                  type="button"
                  className="seq-button"
                  onClick={handleBulkSearch}
                  disabled={bulkLoading}
                >
                  {bulkLoading ? "一括逆引き中..." : "貼り付けたプライマーをペアで評価"}
                </button>
              </div>

              <JobProgressCard
                title="一括 BLAST"
                jobId={bulkJobId}
                job={bulkJobInfo}
                onCancel={bulkJobId ? cancelBulkJob : null}
                cancelDisabled={!bulkJobId}
              />
              {bulkWarning && <p className="seq-hint">注意: {bulkWarning}</p>}
              {bulkError && <p className="seq-error">エラー: {bulkError}</p>}
            </>
          ) : (
            <>
              <p className="panel-hint">
                個別のプライマーペアについて、詳細な BLAST ヒットと予測 PCR 産物を確認します（向き F/R は気にしなくて OK）。
              </p>

              <label className="seq-label">
                プライマー1 (5&apos;→3&apos;):
                <input
                  type="text"
                  className="seq-input"
                  value={primer1}
                  onChange={(e) => setPrimer1(e.target.value)}
                  placeholder="例: ATGCGT..."
                />
              </label>
              <label className="seq-label">
                プライマー2 (5&apos;→3&apos;):
                <input
                  type="text"
                  className="seq-input"
                  value={primer2}
                  onChange={(e) => setPrimer2(e.target.value)}
                  placeholder="例: TGCATC..."
                />
              </label>

              <button
                type="button"
                className="seq-button"
                onClick={handleSearch}
                disabled={loading}
              >
                {loading ? "BLAST 実行中..." : "このプライマーペアで逆引きする"}
              </button>

              {useStandard ? (
                <JobProgressCard
                  title="BLAST（Standard）"
                  jobId={singleJobIdStandard}
                  job={singleJobInfoStandard}
                  onCancel={singleJobIdStandard ? cancelSingleJobStandard : null}
                  cancelDisabled={!singleJobIdStandard}
                />
              ) : null}

              {error && <p className="seq-error">エラー: {error}</p>}
            </>
          )}

          <details className="ui-details">
            <summary>共通設定（DB / 産物長 / BLAST パラメータ）</summary>
            <div className="ui-details-body">
              <div className="primer-row">
                <label className="seq-label">
                  産物長フィルタ（bp）:
                  <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
                    <input
                      type="number"
                      className="seq-input"
                      style={{ width: "90px" }}
                      value={productMin}
                      min={10}
                      onChange={(e) =>
                        setProductMin(Math.max(10, Number(e.target.value) || 10))
                      }
                      disabled={busy}
                    />
                    <span>〜</span>
                    <input
                      type="number"
                      className="seq-input"
                      style={{ width: "90px" }}
                      value={productMax}
                      min={productMin}
                      onChange={(e) =>
                        setProductMax(
                          Math.max(productMin, Number(e.target.value) || productMin),
                        )
                      }
                      disabled={busy}
                    />
                  </div>
                </label>
              </div>

              <div className="seq-label">
                <div className="blast-backend-row" style={{ flexDirection: "row", gap: "1rem" }}>
                  <span>ローカル BLAST DB（複数選択可）:</span>
                  {localDbOptionsWithVirtual.map((opt) => (
                    <label key={opt.value}>
                      <input
                        type="checkbox"
                        checked={selectedLocalDbs.includes(opt.value)}
                        onChange={() => toggleLocalDb(opt.value)}
                        disabled={busy}
                      />{" "}
                      {opt.label}
                    </label>
                  ))}
                </div>
                <div className="primer-row">
                  <input
                    type="text"
                    className="seq-input"
                    value={customLocalDb}
                    onChange={(e) => setCustomLocalDb(e.target.value)}
                    placeholder="追加の makeblastdb prefix (任意)"
                    disabled={busy}
                  />
                </div>
                <div className="tag-row">
                  <span className="tag-label">選択中</span>
                  <code className="tag-db">
                    {localDbViews.length ? localDbViews.map((v) => v.label).join(", ") : "-"}
                  </code>
                </div>
                <span className="seq-hint">
                  BLAST DB base: {DEFAULT_BLAST_DB_BASE} ／ num_threads:{" "}
                  {blastNumThreads != null ? blastNumThreads : "自動 (CPU に応じて最大24、複数DBは自動で割り当て)"}
                </span>
                {wantsqueryTorefView ? (
                  <span className="seq-hint">
                    {query_TO_ref_VIRTUAL_DB_LABEL} は UserDB_query のヒットを ref 座標へ BLAST で対応づけて表示する仮想DBです（検索自体は UserDB_query を使います）。
                  </span>
                ) : null}
              </div>

              <div className="primer-row" style={{ gap: "0.75rem", flexWrap: "wrap" }}>
                <label className="seq-label" style={{ maxWidth: "180px" }}>
                  task:
                  <select
                    className="seq-input"
                    value={blastTask}
                    onChange={(e) => setBlastTask(e.target.value)}
                    disabled={busy}
                  >
                    <option value="blastn-short">blastn-short</option>
                    <option value="blastn">blastn</option>
                    <option value="megablast">megablast</option>
                  </select>
                </label>
                <label className="seq-label" style={{ maxWidth: "180px" }}>
                  E-value:
                  <input
                    type="number"
                    className="seq-input"
                    step="any"
                    value={blastEvalue}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      setBlastEvalue(Number.isNaN(v) ? 10 : v);
                    }}
                    disabled={busy}
                  />
                </label>
                <label className="seq-label" style={{ maxWidth: "180px" }}>
                  max_hsps:
                  <input
                    type="number"
                    className="seq-input"
                    min={1}
                    value={blastMaxHsps ?? ""}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      setBlastMaxHsps(Number.isNaN(v) ? null : Math.max(1, v));
                    }}
                    placeholder="BLASTデフォルト"
                    disabled={busy}
                  />
                </label>
                <label className="seq-label" style={{ maxWidth: "200px" }}>
                  num_threads:
                  <input
                    type="number"
                    className="seq-input"
                    min={1}
                    value={blastNumThreads ?? ""}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      setBlastNumThreads(Number.isNaN(v) ? null : Math.max(1, v));
                    }}
                    placeholder="自動"
                    disabled={busy}
                  />
                  <span className="seq-hint">未指定なら CPU に応じて自動</span>
                </label>
                <label className="seq-label">
                  <input type="checkbox" checked={useStandard} onChange={e => setUseStandard(e.target.checked)} /> Standard
                </label>
                <label className="seq-label" style={{ maxWidth: "200px" }}>
                  local mode:
                  <span className="seq-hint">CPU（通常）</span>
                  <span className="seq-hint">local のときだけ有効</span>
                </label>
              </div>
            </div>
          </details>
        </div>

        <div className="seq-results">
          {mode === "single" ? renderSummaryRow() : null}
          {wantsqueryTorefView && (amplicons.length > 0 || (bulkResults?.length ?? 0) > 0) ? (
            <div className="primer-row" style={{ margin: "0.25rem 0 0.5rem", alignItems: "center" }}>
              <span className="seq-hint">
                {query_TO_ref_VIRTUAL_DB_LABEL}:{" "}
                {queryTorefLoading ? "BLAST変換中..." : queryTorefError ? "BLAST変換失敗" : "BLAST表示ON"}
                {needsqueryTorefXlsxMap
                  ? ` / Excel: ${queryTorefXlsxLoading ? "読込中..." : queryTorefXlsxError ? "読込失敗" : queryTorefXlsxMap ? "OK" : "未読込"
                  }`
                  : ""}
              </span>
            </div>
          ) : null}
          {queryTorefError && <p className="seq-error">エラー: {queryTorefError}</p>}
          {queryTorefXlsxError && <p className="seq-error">エラー: {queryTorefXlsxError}</p>}

          {mode === "bulk" && !bulkLoading && !bulkResults && !bulkError && (
            <p className="seq-hint">
              上の「一括（メイン）」で貼り付けて評価すると、ここに結果が表示されます。
            </p>
          )}

          {mode === "single" && amplicons.length > 0 && (
            <section className="seq-result-block">
              <h3>予測 PCR 産物（ローカル DB）</h3>
              <div className="primer-tabs" style={{ marginBottom: "1rem" }}>
                <button type="button" className={`primer-tab-btn ${resultTab === "standard" ? "is-active" : ""}`} onClick={() => setResultTab("standard")} disabled={!primer1ResultStandard && !bulkResultsStandard} style={{ marginRight: "0.5rem", padding: "4px 12px", borderRadius: "4px", border: "1px solid #ccc", background: resultTab === "standard" ? "#e0e0e0" : "#fff", cursor: "pointer" }}>Standard</button>
              </div>
              <div className="table-scroll">
                <table className="seq-table primer-bulk-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>DB</th>
                      <th>染色体 / コンティグ</th>
                      <th>Gene (local)</th>
                      <th>開始</th>
                      <th>終了</th>
                      <th>長さ (bp)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {amplicons.map((a, idx) => {
                      const allGenes = [
                        ...(a.geneNames ?? []),
                        ...(a.geneIds ?? []),
                      ].filter(Boolean);
                      const uniqueGenes = Array.from(new Set(allGenes));
                      const firstGene = uniqueGenes[0];
                      const extraGenes = uniqueGenes.length > 1 ? uniqueGenes.length - 1 : 0;
                      const baseLabel =
                        firstGene && extraGenes > 0 ? `${firstGene} (+${extraGenes})` : firstGene;
                      const isqueryDb = /query/i.test(a.dbSource);
                      const dbLabel = (a.dbSource || "").replace(/^local:/, "");
                      const isConvertedView = dbLabel === query_TO_ref_VIRTUAL_DB_LABEL;
                      const srcDb = isqueryDb ? localDbLabelToPath.get(dbLabel) : null;
                      const liftover =
                        wantsqueryTorefView && isqueryDb && srcDb
                          ? queryTorefResults[liftoverKey(srcDb, a.subject, a.start, a.end)]
                          : null;
                      const dst = liftover?.dst ?? null;
                      const dstGenes = [
                        ...(dst?.gene_names ?? []),
                        ...(dst?.gene_ids ?? []),
                      ].filter(Boolean);
                      const dstGene = dstGenes[0] ?? null;
                      const queryGeneCandidates = uniqueGenes.filter((g) => /^GENE\\.reference\\.query\\./i.test(g));
                      const queryGene = queryGeneCandidates[0] ?? null;
                      const xlsxMapping =
                        wantsqueryTorefView && isqueryDb && queryGene && queryTorefXlsxMap
                          ? queryTorefXlsxMap.get(normalizequeryrefGeneId(queryGene))
                          : null;
                      const xlsxV1Gene = xlsxMapping?.v1Gene ?? null;
                      const xlsxGeneUrl = xlsxV1Gene ? ensemblGeneUrl(xlsxV1Gene) : null;
                      const xlsxLocUrl = xlsxMapping
                        ? ensemblLocationUrl({
                          species:
                            inferEnsemblPlantsSpecies({ geneId: xlsxV1Gene, dbLabel: "UserDB_ref" }) ||
                            "",
                          chrom: xlsxMapping.v1Chr,
                          start: xlsxMapping.v1Start,
                          end: xlsxMapping.v1End,
                        })
                        : null;
                      const xlsxTranscriptId = xlsxV1Gene ? inferEnsemblTranscriptId({ geneId: xlsxV1Gene }) : null;
                      const xlsxTxSummaryUrl =
                        xlsxMapping && xlsxV1Gene && xlsxTranscriptId
                          ? ensemblTranscriptSummaryUrl({
                            species:
                              inferEnsemblPlantsSpecies({ geneId: xlsxV1Gene, dbLabel: "UserDB_ref" }) ||
                              "",
                            geneId: xlsxV1Gene,
                            transcriptId: xlsxTranscriptId,
                            chrom: xlsxMapping.v1Chr,
                            start: xlsxMapping.v1Start,
                            end: xlsxMapping.v1End,
                          })
                          : null;
                      const xlsxTxExportUrl =
                        xlsxMapping && xlsxV1Gene && xlsxTranscriptId
                          ? ensemblTranscriptExportUrl({
                            species:
                              inferEnsemblPlantsSpecies({ geneId: xlsxV1Gene, dbLabel: "UserDB_ref" }) ||
                              "",
                            geneId: xlsxV1Gene,
                            transcriptId: xlsxTranscriptId,
                            chrom: xlsxMapping.v1Chr,
                            start: xlsxMapping.v1Start,
                            end: xlsxMapping.v1End,
                          })
                          : null;
                      const xlsxRegion = xlsxMapping ? `${xlsxMapping.v1Chr}:${xlsxMapping.v1Start}-${xlsxMapping.v1End}` : null;
                      const url = ensemblGeneUrl(firstGene);
                      const localOnly = isLocalOnlyDb(dbLabel);
                      const locUrl = localOnly
                        ? navigatorLocationUrl({ dbLabel, chrom: a.subject, start: a.start, end: a.end })
                        : ensemblLocationUrl({
                          species: inferEnsemblPlantsSpecies({ geneId: firstGene, dbLabel: a.dbSource }),
                          chrom: a.subject,
                          start: a.start,
                          end: a.end,
                        });
                      const txGeneName =
                        firstGene && /^GENEALT[0-9A-Za-z_]+/.test(firstGene)
                          ? uniqueGenes.find((g) => /^GENE\\d+G\\d+/i.test(g)) ??
                          uniqueGenes.find((g) => /^GENE/i.test(g)) ??
                          null
                          : null;
                      const transcriptId = inferEnsemblTranscriptId({ geneId: firstGene, geneName: txGeneName });
                      const txSummaryUrl =
                        transcriptId && firstGene
                          ? ensemblTranscriptSummaryUrl({
                            species: inferEnsemblPlantsSpecies({ geneId: firstGene, dbLabel: a.dbSource }),
                            geneId: firstGene,
                            transcriptId,
                            chrom: a.subject,
                            start: a.start,
                            end: a.end,
                          })
                          : null;
                      const txExportUrl =
                        transcriptId && firstGene
                          ? ensemblTranscriptExportUrl({
                            species: inferEnsemblPlantsSpecies({ geneId: firstGene, dbLabel: a.dbSource }),
                            geneId: firstGene,
                            transcriptId,
                            chrom: a.subject,
                            start: a.start,
                            end: a.end,
                          })
                          : null;
                      const txRegion = `${a.subject}:${a.start}-${a.end}`;
                      const mappedGeneUrl = dstGene ? ensemblGeneUrl(dstGene) : null;
                      const mappedLocUrl = dst
                        ? ensemblLocationUrl({
                          species:
                            inferEnsemblPlantsSpecies({ geneId: dstGene, dbLabel: "UserDB_ref" }) ||
                            "",
                          chrom: dst.entry || dst.subject_chrom,
                          start: dst.start,
                          end: dst.end,
                        })
                        : null;
                      const mappedTranscriptId = dstGene ? inferEnsemblTranscriptId({ geneId: dstGene }) : null;
                      const mappedTxSummaryUrl =
                        dst && dstGene && mappedTranscriptId
                          ? ensemblTranscriptSummaryUrl({
                            species:
                              inferEnsemblPlantsSpecies({ geneId: dstGene, dbLabel: "UserDB_ref" }) ||
                              "",
                            geneId: dstGene,
                            transcriptId: mappedTranscriptId,
                            chrom: dst.entry || dst.subject_chrom,
                            start: dst.start,
                            end: dst.end,
                          })
                          : null;
                      const mappedTxExportUrl =
                        dst && dstGene && mappedTranscriptId
                          ? ensemblTranscriptExportUrl({
                            species:
                              inferEnsemblPlantsSpecies({ geneId: dstGene, dbLabel: "UserDB_ref" }) ||
                              "",
                            geneId: dstGene,
                            transcriptId: mappedTranscriptId,
                            chrom: dst.entry || dst.subject_chrom,
                            start: dst.start,
                            end: dst.end,
                          })
                          : null;
                      const mappedRegion =
                        dst && (dst.entry || dst.subject_chrom) ? `${dst.entry || dst.subject_chrom}:${dst.start}-${dst.end}` : null;
                      const displaySubject = isConvertedView && dst ? (dst.subject_chrom || dst.entry) : a.subject;
                      const displayStart = isConvertedView && dst ? dst.start : a.start;
                      const displayEnd = isConvertedView && dst ? dst.end : a.end;
                      const displayLength =
                        isConvertedView && dst ? Math.abs(dst.end - dst.start) + 1 : a.length;
                      const displayLocUrl = isConvertedView ? mappedLocUrl : locUrl;
                      return (
                        <tr key={`${a.dbSource}-${a.subject}-${a.start}-${a.end}-${idx}`}>
                          <td>{idx + 1}</td>
                          <td>{dbLabel}</td>
                          <td>
                            {displaySubject}
                            {displayLocUrl ? (
                              <>
                                {" "}
                                (
                                <a href={displayLocUrl} target="_blank" rel="noreferrer">
                                  {isConvertedView ? "External" : (localOnly ? "Navigator" : "External")}
                                </a>
                                )
                              </>
                            ) : null}
                          </td>
                          <td>
                            {isConvertedView ? (
                              <>
                                {dstGene ? (
                                  mappedGeneUrl ? (
                                    <a href={mappedGeneUrl} target="_blank" rel="noreferrer">
                                      {dstGene}
                                    </a>
                                  ) : (
                                    dstGene
                                  )
                                ) : xlsxV1Gene ? (
                                  xlsxGeneUrl ? (
                                    <a href={xlsxGeneUrl} target="_blank" rel="noreferrer">
                                      {xlsxV1Gene}
                                    </a>
                                  ) : (
                                    xlsxV1Gene
                                  )
                                ) : (
                                  "-"
                                )}
                                {!dstGene && xlsxMapping ? (
                                  <div className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                    excel: {formatMappedrefRangeFromXlsx(xlsxMapping)}（{xlsxMapping.confidence}
                                    {xlsxMapping.ambiguous ? ", ambiguous" : ""}）
                                  </div>
                                ) : null}
                                {baseLabel ? (
                                  <div className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                    query:{" "}
                                    {url ? (
                                      <a href={url} target="_blank" rel="noreferrer">
                                        {baseLabel}
                                      </a>
                                    ) : (
                                      baseLabel
                                    )}
                                  </div>
                                ) : null}
                              </>
                            ) : baseLabel ? (
                              url ? (
                                <a href={url} target="_blank" rel="noreferrer">
                                  {baseLabel}
                                </a>
                              ) : (
                                baseLabel
                              )
                            ) : (
                              "-"
                            )}
                            {isConvertedView ? (
                              dst ? (
                                mappedTxSummaryUrl || mappedTxExportUrl ? (
                                  <div className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                    {mappedTxSummaryUrl ? (
                                      <a href={mappedTxSummaryUrl} target="_blank" rel="noreferrer">
                                        Transcript
                                      </a>
                                    ) : null}
                                    {mappedTxSummaryUrl && mappedTxExportUrl ? " / " : null}
                                    {mappedTxExportUrl ? (
                                      <a href={mappedTxExportUrl} target="_blank" rel="noreferrer">
                                        配列取得(FASTA)
                                      </a>
                                    ) : null}
                                    {mappedTxExportUrl && mappedTranscriptId ? (
                                      <>
                                        {" "}
                                        (
                                        <button
                                          type="button"
                                          className="link-button"
                                          onClick={() => {
                                            const sp =
                                              inferEnsemblPlantsSpecies({ geneId: dstGene, dbLabel: "UserDB_ref" }) ||
                                              "";
                                            void downloadEnsemblExportFasta({
                                              speciesPath: sp,
                                              transcriptId: mappedTranscriptId,
                                              geneId: dstGene,
                                              region: mappedRegion,
                                              fileBase: `ensembl_${dstGene}_${mappedTranscriptId}`,
                                            });
                                          }}
                                        >
                                          APIで保存
                                        </button>
                                        )
                                      </>
                                    ) : null}
                                    {mappedTxSummaryUrl && mappedTxExportUrl ? (
                                      <>
                                        {" "}
                                        (
                                        <button
                                          type="button"
                                          className="link-button"
                                          onClick={() => {
                                            window.open(mappedTxSummaryUrl, "_blank", "noopener,noreferrer");
                                            window.open(mappedTxExportUrl, "_blank", "noopener,noreferrer");
                                          }}
                                        >
                                          両方
                                        </button>
                                        )
                                      </>
                                    ) : null}
                                  </div>
                                ) : null
                              ) : xlsxTxSummaryUrl || xlsxTxExportUrl ? (
                                <div className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                  {xlsxTxSummaryUrl ? (
                                    <a href={xlsxTxSummaryUrl} target="_blank" rel="noreferrer">
                                      Transcript
                                    </a>
                                  ) : null}
                                  {xlsxTxSummaryUrl && xlsxTxExportUrl ? " / " : null}
                                  {xlsxTxExportUrl ? (
                                    <a href={xlsxTxExportUrl} target="_blank" rel="noreferrer">
                                      配列取得(FASTA)
                                    </a>
                                  ) : null}
                                  {xlsxTxExportUrl && xlsxTranscriptId ? (
                                    <>
                                      {" "}
                                      (
                                      <button
                                        type="button"
                                        className="link-button"
                                        onClick={() => {
                                          const sp =
                                            inferEnsemblPlantsSpecies({ geneId: xlsxV1Gene, dbLabel: "UserDB_ref" }) ||
                                            "";
                                          void downloadEnsemblExportFasta({
                                            speciesPath: sp,
                                            transcriptId: xlsxTranscriptId,
                                            geneId: xlsxV1Gene,
                                            region: xlsxRegion,
                                            fileBase: `ensembl_${xlsxV1Gene}_${xlsxTranscriptId}`,
                                          });
                                        }}
                                      >
                                        APIで保存
                                      </button>
                                      )
                                    </>
                                  ) : null}
                                  {xlsxTxSummaryUrl && xlsxTxExportUrl ? (
                                    <>
                                      {" "}
                                      (
                                      <button
                                        type="button"
                                        className="link-button"
                                        onClick={() => {
                                          window.open(xlsxTxSummaryUrl, "_blank", "noopener,noreferrer");
                                          window.open(xlsxTxExportUrl, "_blank", "noopener,noreferrer");
                                        }}
                                      >
                                        両方
                                      </button>
                                      )
                                    </>
                                  ) : null}
                                </div>
                              ) : null
                            ) : txSummaryUrl || txExportUrl ? (
                              <div className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                {txSummaryUrl ? (
                                  <a href={txSummaryUrl} target="_blank" rel="noreferrer">
                                    Transcript
                                  </a>
                                ) : null}
                                {txSummaryUrl && txExportUrl ? " / " : null}
                                {txExportUrl ? (
                                  <a href={txExportUrl} target="_blank" rel="noreferrer">
                                    配列取得(FASTA)
                                  </a>
                                ) : null}
                                {txExportUrl && transcriptId ? (
                                  <>
                                    {" "}
                                    (
                                    <button
                                      type="button"
                                      className="link-button"
                                      onClick={() => {
                                        const sp = inferEnsemblPlantsSpecies({ geneId: firstGene, dbLabel: a.dbSource });
                                        if (!sp) return;
                                        void downloadEnsemblExportFasta({
                                          speciesPath: sp,
                                          transcriptId,
                                          geneId: firstGene,
                                          region: txRegion,
                                          fileBase: `ensembl_${firstGene}_${transcriptId}`,
                                        });
                                      }}
                                    >
                                      APIで保存
                                    </button>
                                    )
                                  </>
                                ) : null}
                                {txSummaryUrl && txExportUrl ? (
                                  <>
                                    {" "}
                                    (
                                    <button
                                      type="button"
                                      className="link-button"
                                      onClick={() => {
                                        window.open(txSummaryUrl, "_blank", "noopener,noreferrer");
                                        window.open(txExportUrl, "_blank", "noopener,noreferrer");
                                      }}
                                    >
                                      両方
                                    </button>
                                    )
                                  </>
                                ) : null}
                              </div>
                            ) : null}
                            {dst && !isConvertedView ? (
                              <div className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                ref:{" "}
                                {mappedLocUrl ? (
                                  <a href={mappedLocUrl} target="_blank" rel="noreferrer">
                                    {formatMappedrefRange(dst)}
                                  </a>
                                ) : (
                                  formatMappedrefRange(dst)
                                )}
                                {", "}
                                {mappedGeneUrl ? (
                                  <a href={mappedGeneUrl} target="_blank" rel="noreferrer">
                                    {dstGene}
                                  </a>
                                ) : (
                                  dstGene || "-"
                                )}
                                {mappedTxSummaryUrl || mappedTxExportUrl ? (
                                  <>
                                    {" "}
                                    /{" "}
                                    {mappedTxSummaryUrl ? (
                                      <a href={mappedTxSummaryUrl} target="_blank" rel="noreferrer">
                                        Transcript
                                      </a>
                                    ) : null}
                                    {mappedTxSummaryUrl && mappedTxExportUrl ? " / " : null}
                                    {mappedTxExportUrl ? (
                                      <a href={mappedTxExportUrl} target="_blank" rel="noreferrer">
                                        配列取得(FASTA)
                                      </a>
                                    ) : null}
                                    {mappedTxExportUrl && mappedTranscriptId ? (
                                      <>
                                        {" "}
                                        (
                                        <button
                                          type="button"
                                          className="link-button"
                                          onClick={() => {
                                            const sp =
                                              inferEnsemblPlantsSpecies({ geneId: dstGene, dbLabel: "UserDB_ref" }) ||
                                              "";
                                            void downloadEnsemblExportFasta({
                                              speciesPath: sp,
                                              transcriptId: mappedTranscriptId,
                                              geneId: dstGene,
                                              region: mappedRegion,
                                              fileBase: `ensembl_${dstGene}_${mappedTranscriptId}`,
                                            });
                                          }}
                                        >
                                          APIで保存
                                        </button>
                                        )
                                      </>
                                    ) : null}
                                  </>
                                ) : null}
                                {liftover?.note ? ` / 注意:${liftover.note}` : null}
                              </div>
                            ) : null}
                            {!dst && !isConvertedView && xlsxMapping ? (
                              <div className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                ref(excel):{" "}
                                {xlsxLocUrl ? (
                                  <a href={xlsxLocUrl} target="_blank" rel="noreferrer">
                                    {formatMappedrefRangeFromXlsx(xlsxMapping)}
                                  </a>
                                ) : (
                                  formatMappedrefRangeFromXlsx(xlsxMapping)
                                )}
                                {", "}
                                {xlsxGeneUrl ? (
                                  <a href={xlsxGeneUrl} target="_blank" rel="noreferrer">
                                    {xlsxV1Gene}
                                  </a>
                                ) : (
                                  xlsxV1Gene || "-"
                                )}
                                {xlsxTxSummaryUrl || xlsxTxExportUrl ? (
                                  <>
                                    {" "}
                                    /{" "}
                                    {xlsxTxSummaryUrl ? (
                                      <a href={xlsxTxSummaryUrl} target="_blank" rel="noreferrer">
                                        Transcript
                                      </a>
                                    ) : null}
                                    {xlsxTxSummaryUrl && xlsxTxExportUrl ? " / " : null}
                                    {xlsxTxExportUrl ? (
                                      <a href={xlsxTxExportUrl} target="_blank" rel="noreferrer">
                                        配列取得(FASTA)
                                      </a>
                                    ) : null}
                                    {xlsxTxExportUrl && xlsxTranscriptId ? (
                                      <>
                                        {" "}
                                        (
                                        <button
                                          type="button"
                                          className="link-button"
                                          onClick={() => {
                                            const sp =
                                              inferEnsemblPlantsSpecies({ geneId: xlsxV1Gene, dbLabel: "UserDB_ref" }) ||
                                              "";
                                            void downloadEnsemblExportFasta({
                                              speciesPath: sp,
                                              transcriptId: xlsxTranscriptId,
                                              geneId: xlsxV1Gene,
                                              region: xlsxRegion,
                                              fileBase: `ensembl_${xlsxV1Gene}_${xlsxTranscriptId}`,
                                            });
                                          }}
                                        >
                                          APIで保存
                                        </button>
                                        )
                                      </>
                                    ) : null}
                                  </>
                                ) : null}
                                {`（${xlsxMapping.confidence}${xlsxMapping.ambiguous ? ", ambiguous" : ""}）`}
                              </div>
                            ) : null}
                            {dst && isConvertedView && liftover?.note ? (
                              <div className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                注意: {liftover.note}
                              </div>
                            ) : null}
                          </td>
                          <td>{displayStart.toLocaleString()}</td>
                          <td>{displayEnd.toLocaleString()}</td>
                          <td>{displayLength.toLocaleString()}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {mode === "single" && !loading && primer1Result && primer2Result && amplicons.length === 0 && (
            <p className="seq-hint">
              ローカル DB に対して、このプライマーペアで条件を満たす予測 PCR 産物は見つかりませんでした（ヒット位置が離れすぎているか、同じコンティグに並んでいない可能性があります）。
            </p>
          )}

          {mode === "single" && !loading && !primer1Result && !primer2Result && !error && (
            <p className="seq-hint">
              2 本のプライマー配列とローカル BLAST DB を指定し、「このプライマーペアで逆引きする」を押すと、ここに予測 PCR 産物の一覧が表示されます。
            </p>
          )}

          {mode === "bulk" && bulkResults && bulkResults.length > 0 && (
            <section className="seq-result-block">
              <h3>一括評価結果（複数ペア）</h3>
              <div className="primer-tabs" style={{ marginBottom: "1rem" }}>
                <button type="button" className={`primer-tab-btn ${resultTab === "standard" ? "is-active" : ""}`} onClick={() => setResultTab("standard")} disabled={!primer1ResultStandard && !bulkResultsStandard} style={{ marginRight: "0.5rem", padding: "4px 12px", borderRadius: "4px", border: "1px solid #ccc", background: resultTab === "standard" ? "#e0e0e0" : "#fff", cursor: "pointer" }}>Standard</button>
              </div>
              <div className="table-scroll">
                <table className="seq-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Name1</th>
                      <th>Name2</th>
                      <th>Primer1 (5&apos;→3&apos;)</th>
                      <th>Primer2 (5&apos;→3&apos;)</th>
                      {bulkDbLabels.map((db) => (
                        <React.Fragment key={db}>
                          <th>{db}ヒット数</th>
                          <th>{db}座標,gene</th>
                          <th>{db}産物長</th>
                          <th>{db}品質</th>
                        </React.Fragment>
                      ))}
                      <th>品質</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bulkResults.map((r) => {
                      const perDbMap = new Map(r.perDb.map((d) => [d.db, d]));

                      const renderQualityBadge = (q?: string) =>
                        q ? (
                          <span className={`quality-badge grade-${q.toLowerCase()}`}>
                            {q}
                          </span>
                        ) : (
                          "-"
                        );

                      const coordGene = (d?: (typeof r.perDb)[number]) => {
                        if (!d || !d.topAmplicons.length) return "-";
                        const isConvertedView = d.db === query_TO_ref_VIRTUAL_DB_LABEL;
                        const isqueryDb = /query/i.test(d.db);

                        const parts = d.topAmplicons.map((a, idx) => {
                          const coordquery = `${a.subject}:${a.start}-${a.end} (${a.length}bp)`;
                          const genequery = a.geneLabel;
                          const urlquery = ensemblGeneUrl(genequery);
                          const localOnlyquery = isLocalOnlyDb(d.db);
                          const locUrlquery = localOnlyquery
                            ? navigatorLocationUrl({ dbLabel: d.db, chrom: a.subject, start: a.start, end: a.end })
                            : ensemblLocationUrl({
                              species: inferEnsemblPlantsSpecies({ geneId: genequery, dbLabel: d.db }),
                              chrom: a.subject,
                              start: a.start,
                              end: a.end,
                            });

                          const srcDb = isqueryDb ? localDbLabelToPath.get(d.db) : null;
                          const liftover =
                            wantsqueryTorefView && isqueryDb && srcDb
                              ? queryTorefResults[liftoverKey(srcDb, a.subject, a.start, a.end)]
                              : null;
                          const dst = liftover?.dst ?? null;
                          const dstGenes = [
                            ...(dst?.gene_names ?? []),
                            ...(dst?.gene_ids ?? []),
                          ].filter(Boolean);
                          const dstGene = dstGenes[0] ?? null;
                          const xlsxMapping =
                            wantsqueryTorefView && isqueryDb && genequery && queryTorefXlsxMap && /^GENE\\.reference\\.query\\./i.test(genequery)
                              ? queryTorefXlsxMap.get(normalizequeryrefGeneId(genequery))
                              : null;
                          const xlsxV1Gene = xlsxMapping?.v1Gene ?? null;
                          const xlsxGeneUrl = xlsxV1Gene ? ensemblGeneUrl(xlsxV1Gene) : null;
                          const xlsxLocUrl = xlsxMapping
                            ? ensemblLocationUrl({
                              species:
                                inferEnsemblPlantsSpecies({ geneId: xlsxV1Gene, dbLabel: "UserDB_ref" }) ||
                                "",
                              chrom: xlsxMapping.v1Chr,
                              start: xlsxMapping.v1Start,
                              end: xlsxMapping.v1End,
                            })
                            : null;
                          const mappedGeneUrl = dstGene ? ensemblGeneUrl(dstGene) : null;
                          const mappedLocUrl = dst
                            ? ensemblLocationUrl({
                              species:
                                inferEnsemblPlantsSpecies({ geneId: dstGene, dbLabel: "UserDB_ref" }) ||
                                "",
                              chrom: dst.entry || dst.subject_chrom,
                              start: dst.start,
                              end: dst.end,
                            })
                            : null;

                          const primaryCoord = isConvertedView && dst ? formatMappedrefRange(dst) : coordquery;
                          const primaryLocUrl = isConvertedView && dst ? mappedLocUrl : locUrlquery;

                          const primaryGene = isConvertedView && dstGene ? dstGene : genequery;
                          const primaryGeneUrl = isConvertedView && dstGene ? mappedGeneUrl : urlquery;

                          return (
                            // eslint-disable-next-line react/no-array-index-key
                            <span key={`${a.subject}-${a.start}-${a.end}-${idx}`}>
                              {primaryLocUrl ? (
                                <a href={primaryLocUrl} target="_blank" rel="noreferrer">
                                  {primaryCoord}
                                </a>
                              ) : (
                                primaryCoord
                              )}
                              {primaryGene ? (
                                <>
                                  {", "}
                                  {primaryGeneUrl ? (
                                    <a href={primaryGeneUrl} target="_blank" rel="noreferrer">
                                      {primaryGene}
                                    </a>
                                  ) : (
                                    primaryGene
                                  )}
                                </>
                              ) : null}
                              {isConvertedView && dst ? (
                                <span className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                  query:{" "}
                                  {locUrlquery ? (
                                    <a href={locUrlquery} target="_blank" rel="noreferrer">
                                      {coordquery}
                                    </a>
                                  ) : (
                                    coordquery
                                  )}
                                  {genequery ? (
                                    <>
                                      {", "}
                                      {urlquery ? (
                                        <a href={urlquery} target="_blank" rel="noreferrer">
                                          {genequery}
                                        </a>
                                      ) : (
                                        genequery
                                      )}
                                    </>
                                  ) : null}
                                  {liftover?.note ? ` / 注意:${liftover.note}` : null}
                                </span>
                              ) : null}
                              {isConvertedView && !dst && xlsxMapping ? (
                                <span className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                  excel:{" "}
                                  {xlsxLocUrl ? (
                                    <a href={xlsxLocUrl} target="_blank" rel="noreferrer">
                                      {formatMappedrefRangeFromXlsx(xlsxMapping)}
                                    </a>
                                  ) : (
                                    formatMappedrefRangeFromXlsx(xlsxMapping)
                                  )}
                                  {xlsxV1Gene ? (
                                    <>
                                      {", "}
                                      {xlsxGeneUrl ? (
                                        <a href={xlsxGeneUrl} target="_blank" rel="noreferrer">
                                          {xlsxV1Gene}
                                        </a>
                                      ) : (
                                        xlsxV1Gene
                                      )}
                                    </>
                                  ) : null}
                                  {`（${xlsxMapping.confidence}${xlsxMapping.ambiguous ? ", ambiguous" : ""}）`}
                                </span>
                              ) : null}
                              {!isConvertedView && dst ? (
                                <span className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                  ref:{" "}
                                  {mappedLocUrl ? (
                                    <a href={mappedLocUrl} target="_blank" rel="noreferrer">
                                      {formatMappedrefRange(dst)}
                                    </a>
                                  ) : (
                                    formatMappedrefRange(dst)
                                  )}
                                  {", "}
                                  {mappedGeneUrl ? (
                                    <a href={mappedGeneUrl} target="_blank" rel="noreferrer">
                                      {dstGene}
                                    </a>
                                  ) : (
                                    dstGene
                                  )}
                                  {liftover?.note ? ` / 注意:${liftover.note}` : null}
                                </span>
                              ) : null}
                              {!isConvertedView && !dst && xlsxMapping ? (
                                <span className="seq-hint" style={{ display: "block", fontSize: "0.78rem" }}>
                                  ref(excel):{" "}
                                  {xlsxLocUrl ? (
                                    <a href={xlsxLocUrl} target="_blank" rel="noreferrer">
                                      {formatMappedrefRangeFromXlsx(xlsxMapping)}
                                    </a>
                                  ) : (
                                    formatMappedrefRangeFromXlsx(xlsxMapping)
                                  )}
                                  {xlsxV1Gene ? (
                                    <>
                                      {", "}
                                      {xlsxGeneUrl ? (
                                        <a href={xlsxGeneUrl} target="_blank" rel="noreferrer">
                                          {xlsxV1Gene}
                                        </a>
                                      ) : (
                                        xlsxV1Gene
                                      )}
                                    </>
                                  ) : null}
                                  {`（${xlsxMapping.confidence}${xlsxMapping.ambiguous ? ", ambiguous" : ""}）`}
                                </span>
                              ) : null}
                              {idx < d.topAmplicons.length - 1 ? " | " : null}
                            </span>
                          );
                        });

                        const extraCount =
                          d.ampliconCount > TOP_AMPLICONS_PER_DB
                            ? d.ampliconCount - TOP_AMPLICONS_PER_DB
                            : 0;
                        return (
                          <>
                            {parts}
                            {extraCount > 0 ? ` (+${extraCount})` : null}
                          </>
                        );
                      };

                      const lenText = (d?: (typeof r.perDb)[number]) =>
                        d && d.topAmplicons.length ? d.topAmplicons[0].length.toString() : "-";

                      return (
                        <tr key={r.index}>
                          <td>{r.index}</td>
                          <td>{r.name1 ?? "-"}</td>
                          <td>{r.name2 ?? "-"}</td>
                          <td>{r.primer1}</td>
                          <td>{r.primer2}</td>
                          {bulkDbLabels.map((db) => {
                            const d = perDbMap.get(db);
                            return (
                              <React.Fragment key={db}>
                                <td>{d?.ampliconCount ?? 0}</td>
                                <td>{coordGene(d)}</td>
                                <td>{lenText(d)}</td>
                                <td>{renderQualityBadge(d?.quality)}</td>
                              </React.Fragment>
                            );
                          })}
                          <td>{renderQualityBadge(r.quality)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
      </div>
    </section>
  );
}



