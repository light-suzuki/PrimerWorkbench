import React, { useEffect, useMemo, useState } from "react";
import { bioapiClient } from "../api/bioapiClient";
import type { BlastResponse } from "../types/blast";
import type { JobInfo } from "../types/jobs";
import type { PrimerDesignResponse, PrimerPair } from "../types/primers";
import { computePrimerAmplicons, type PredictedAmplicon } from "../utils/primerBlast";
import { runBlastBatchLocalJob } from "../utils/blastBatchLocalJob";
import {
  DEFAULT_BLAST_DB_BASE,
  labelForDbPath,
  relabelLocalBlastHits,
  useLocalBlastDbOptions,
  usePreferredLocalDbPaths,
  normalizeLocalDbValue,
} from "../utils/localBlastDbs";
import { useLocalBlastMode } from "../utils/localBlastMode";
import { normalizePrimerSeq } from "../utils/primerInput";
import { useToast } from "./ToastProvider";
import { FeatureSequenceView, type PrimerRange1Based } from "./FeatureSequenceView";
import { JobProgressCard } from "./JobProgressCard";

type PrimerScreenSummary = {
  idx: number;
  pair: PrimerPair;
  leftSeq: string;
  rightSeq: string;
  leftHits: number;
  rightHits: number;
  ampTotal: number;
  perDbAmplicons: Record<string, number>;
  dbOk: number;
  dbMissing: number;
  dbTooMany: number;
};

const normalizeDnaSequence = (text: string): string =>
  (text || "")
    .split(/\r?\n/)
    .filter((l) => !l.trim().startsWith(">"))
    .join("")
    .replace(/\s+/g, "")
    .toUpperCase()
    .replace(/[^ACGTURYKMSWBDHVN]/g, "")
    .replace(/U/g, "T");

const uniq = <T,>(arr: T[]): T[] => Array.from(new Set(arr));

const geneLabelForAmplicon = (amp: PredictedAmplicon): string => {
  const all = [...(amp.geneNames ?? []), ...(amp.geneIds ?? [])].filter(Boolean);
  const genes = uniq(all);
  if (!genes.length) return "";
  if (genes.length === 1) return genes[0] ?? "";
  return `${genes[0]} (+${genes.length - 1})`;
};

const dbLabelFromSource = (src: string): string => (src || "").replace(/^local:/, "");

const clampRange = (min: number, max: number): { min: number; max: number } => {
  const lo = Math.max(1, Number.isFinite(min) ? Math.floor(min) : 1);
  const hi = Math.max(lo, Number.isFinite(max) ? Math.floor(max) : lo);
  return { min: lo, max: hi };
};

const dedupePaths = (paths: string[]): string[] => {
  const out: string[] = [];
  const seen = new Set<string>();
  paths.forEach((raw) => {
    const p = (raw || "").trim();
    if (!p) return;
    if (seen.has(p)) return;
    seen.add(p);
    out.push(p);
  });
  return out;
};

export const PrimerBlastPanel: React.FC = () => {
  const { showToast } = useToast();
  const { options: localDbOptions, loading: localDbLoading, error: localDbError } =
    useLocalBlastDbOptions();

  const [selectedLocalDbs, setSelectedLocalDbs, resetSelectedLocalDbs] =
    usePreferredLocalDbPaths();
  const [customLocalDb, setCustomLocalDb] = useState<string>("");
  const [localMode, setLocalMode] = useLocalBlastMode();

  const [sequenceText, setSequenceText] = useState<string>("");
  const normalizedInputSequence = useMemo(
    () => normalizeDnaSequence(sequenceText),
    [sequenceText],
  );

  const [productMin, setProductMin] = useState<number>(200);
  const [productMax, setProductMax] = useState<number>(1000);
  const requestedProductRange = useMemo(
    () => clampRange(productMin, productMax),
    [productMax, productMin],
  );
  const templateLength = normalizedInputSequence.length;
  const effectiveProductRange = useMemo(() => {
    if (!templateLength) return requestedProductRange;
    return {
      min: requestedProductRange.min,
      max: Math.min(requestedProductRange.max, templateLength),
    };
  }, [requestedProductRange, templateLength]);

  const [numReturn, setNumReturn] = useState<number>(10);
  const [screenTopN, setScreenTopN] = useState<number>(20);

  const [optTm, setOptTm] = useState<number>(60.0);
  const [minTm, setMinTm] = useState<number>(57.0);
  const [maxTm, setMaxTm] = useState<number>(63.0);

  const [blastTask, setBlastTask] = useState<string>("blastn-short");
  const [blastEvalue, setBlastEvalue] = useState<number>(10);
  const [blastMaxHits, setBlastMaxHits] = useState<number>(25);
  const [blastMaxHsps, setBlastMaxHsps] = useState<number | null>(null);
  const [blastNumThreads, setBlastNumThreads] = useState<number | null>(null);

  const [filterNoOfftarget, setFilterNoOfftarget] = useState<boolean>(false);
  const [filterRequireAllDbs, setFilterRequireAllDbs] = useState<boolean>(false);

  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [blastJobId, setBlastJobId] = useState<string | null>(null);
  const [blastJobInfo, setBlastJobInfo] = useState<JobInfo | null>(null);

  const [normalizedSequence, setNormalizedSequence] = useState<string>("");
  const [designResult, setDesignResult] = useState<PrimerDesignResponse | null>(null);
  const [screenDbPaths, setScreenDbPaths] = useState<string[]>([]);
  const [screenDbLabels, setScreenDbLabels] = useState<string[]>([]);
  const [blastByPrimerSeq, setBlastByPrimerSeq] = useState<Map<string, BlastResponse> | null>(null);
  const [summaries, setSummaries] = useState<PrimerScreenSummary[]>([]);
  const [selectedRow, setSelectedRow] = useState<number | null>(null);

  const localDbPathsToQuery = useMemo(() => {
    const out: string[] = [...selectedLocalDbs];
    const custom = normalizeLocalDbValue(customLocalDb);
    if (custom) out.push(custom);
    return dedupePaths(out);
  }, [customLocalDb, selectedLocalDbs]);

  const toggleLocalDb = (path: string) => {
    setSelectedLocalDbs((prev) =>
      prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path],
    );
  };

  const runPrimerBlast = async () => {
    const dbs = localDbPathsToQuery;
    if (!normalizedInputSequence) {
      setError("テンプレート配列（DNA）を貼り付けてください（FASTA も可）。");
      return;
    }
    if (dbs.length === 0) {
      setError("ローカル BLAST DB を少なくとも 1 つ選択してください。");
      return;
    }
    if (effectiveProductRange.min > effectiveProductRange.max) {
      setError(
        `テンプレート配列が短すぎます（テンプレ長: ${templateLength.toLocaleString()} bp / 産物長 min: ${effectiveProductRange.min.toLocaleString()} bp）。産物長 min を下げてください。`,
      );
      return;
    }

    setLoading(true);
    setError(null);
    setDesignResult(null);
    setSummaries([]);
    setSelectedRow(null);
    setBlastByPrimerSeq(null);
    setScreenDbPaths([]);
    setScreenDbLabels([]);
    setBlastJobId(null);
    setBlastJobInfo(null);

    try {
      const productSizeRange = `${effectiveProductRange.min}-${effectiveProductRange.max}`;
      const design = await bioapiClient.designPrimers({
        sequence: normalizedInputSequence,
        num_return: Math.max(1, Math.min(200, numReturn)),
        product_size_range: productSizeRange,
        opt_tm: optTm,
        min_tm: minTm,
        max_tm: maxTm,
      });

      setNormalizedSequence(normalizedInputSequence);
      setDesignResult(design);

      const candidates = (design.candidates ?? []).slice(
        0,
        Math.max(1, Math.min(design.candidates.length, screenTopN)),
      );
      if (!candidates.length) {
        setError("プライマー候補が見つかりませんでした（条件を緩めて再試行してください）。");
        return;
      }

      const uniqPrimerSeqs: string[] = [];
      const idxBySeq = new Map<string, number>();
      const indexOf = (raw: string): string => {
        const key = normalizePrimerSeq(raw);
        if (!key) return "";
        if (idxBySeq.has(key)) return key;
        idxBySeq.set(key, uniqPrimerSeqs.length);
        uniqPrimerSeqs.push(key);
        return key;
      };

      const pairRefs = candidates.map((pair, idx) => ({
        idx,
        pair,
        left: indexOf(pair.left_sequence),
        right: indexOf(pair.right_sequence),
      }));

      const { result: batch } = await runBlastBatchLocalJob(
        {
          sequences: uniqPrimerSeqs,
          dbs,
          local_mode: localMode,
          task: blastTask,
          evalue: blastEvalue,
          max_target_seqs: blastMaxHits,
          max_hsps: blastMaxHsps ?? undefined,
          num_threads: blastNumThreads ?? undefined,
          engine: "blast",
        },
        {
          onCreated: (id) => setBlastJobId(id),
          onUpdate: (info) => setBlastJobInfo(info),
          intervalMs: 900,
        },
      );
      if (!batch.results || batch.results.length !== uniqPrimerSeqs.length) {
        throw new Error("BLAST の結果件数が期待値と一致しませんでした。");
      }

      const blastMap = new Map<string, BlastResponse>();
      for (let i = 0; i < uniqPrimerSeqs.length; i += 1) {
        const seq = uniqPrimerSeqs[i];
        const merged = batch.results[i];
        const hits = relabelLocalBlastHits(merged?.hits ?? [], dbs, localDbOptions);
        blastMap.set(seq, { num_hits: hits.length, hits });
      }

      const dbLabels = dbs.map((p) => labelForDbPath(p, localDbOptions));
      setScreenDbPaths(dbs);
      setScreenDbLabels(dbLabels);
      setBlastByPrimerSeq(blastMap);

      const nextSummaries: PrimerScreenSummary[] = pairRefs.map((ref) => {
        const leftRes = blastMap.get(ref.left) ?? { num_hits: 0, hits: [] };
        const rightRes = blastMap.get(ref.right) ?? { num_hits: 0, hits: [] };
        const { amplicons } = computePrimerAmplicons(leftRes, rightRes);
        const ampsInRange = amplicons.filter(
          (a) =>
            a.length >= effectiveProductRange.min && a.length <= effectiveProductRange.max,
        );
        const perDbAmplicons: Record<string, number> = Object.fromEntries(
          dbLabels.map((l) => [l, 0]),
        );
        ampsInRange.forEach((a) => {
          const lab = dbLabelFromSource(a.dbSource);
          perDbAmplicons[lab] = (perDbAmplicons[lab] ?? 0) + 1;
        });

        let dbOk = 0;
        let dbMissing = 0;
        let dbTooMany = 0;
        dbLabels.forEach((lab) => {
          const n = perDbAmplicons[lab] ?? 0;
          if (n === 1) dbOk += 1;
          else if (n === 0) dbMissing += 1;
          else dbTooMany += 1;
        });

        return {
          idx: ref.idx,
          pair: ref.pair,
          leftSeq: ref.left,
          rightSeq: ref.right,
          leftHits: leftRes.num_hits,
          rightHits: rightRes.num_hits,
          ampTotal: ampsInRange.length,
          perDbAmplicons,
          dbOk,
          dbMissing,
          dbTooMany,
        };
      });

      nextSummaries.sort((a, b) => {
        if (a.dbTooMany !== b.dbTooMany) return a.dbTooMany - b.dbTooMany;
        if (a.dbMissing !== b.dbMissing) return a.dbMissing - b.dbMissing;
        if (a.ampTotal !== b.ampTotal) return a.ampTotal - b.ampTotal;
        const ap = a.pair.pair_penalty ?? 9999;
        const bp = b.pair.pair_penalty ?? 9999;
        if (ap !== bp) return ap - bp;
        return a.idx - b.idx;
      });

      setSummaries(nextSummaries);
      showToast("PrimerBLAST が完了しました", "success");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "PrimerBLAST に失敗しました。";
      setError(msg);
      showToast(msg, "error");
    } finally {
      setLoading(false);
    }
  };

  const cancelBlastJob = async () => {
    if (!blastJobId) return;
    try {
      const info = await bioapiClient.cancelJob(blastJobId);
      setBlastJobInfo(info);
      showToast("キャンセルを要求しました（実行中の BLAST はすぐ止まらないことがあります）", "info");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "キャンセルに失敗しました。";
      showToast(msg, "error");
    }
  };

  const displayedSummaries = useMemo(() => {
    const list = summaries.slice();
    return list.filter((s) => {
      if (filterNoOfftarget && s.dbTooMany > 0) return false;
      if (filterRequireAllDbs && s.dbMissing > 0) return false;
      return true;
    });
  }, [filterNoOfftarget, filterRequireAllDbs, summaries]);

  useEffect(() => {
    if (!displayedSummaries.length) {
      setSelectedRow(null);
      return;
    }
    if (selectedRow == null || selectedRow >= displayedSummaries.length) {
      setSelectedRow(0);
    }
  }, [displayedSummaries, selectedRow]);

  const selectedSummary = selectedRow != null ? displayedSummaries[selectedRow] : null;

  const selectedAmplicons = useMemo(() => {
    if (!selectedSummary || !blastByPrimerSeq) return [];
    const leftRes = blastByPrimerSeq.get(selectedSummary.leftSeq) ?? { num_hits: 0, hits: [] };
    const rightRes = blastByPrimerSeq.get(selectedSummary.rightSeq) ?? { num_hits: 0, hits: [] };
    const { amplicons } = computePrimerAmplicons(leftRes, rightRes);
    const filtered = amplicons.filter(
      (a) =>
        a.length >= effectiveProductRange.min && a.length <= effectiveProductRange.max,
    );
    filtered.sort((a, b) => {
      const da = dbLabelFromSource(a.dbSource);
      const db = dbLabelFromSource(b.dbSource);
      if (da !== db) return da.localeCompare(db);
      if (a.subject !== b.subject) return a.subject.localeCompare(b.subject);
      return a.start - b.start;
    });
    return filtered;
  }, [
    blastByPrimerSeq,
    effectiveProductRange.max,
    effectiveProductRange.min,
    selectedSummary,
  ]);

  const selectedPrimerRanges = useMemo((): PrimerRange1Based[] => {
    if (!selectedSummary || !normalizedSequence) return [];
    const pair = selectedSummary.pair;
    const rightStart = pair.right_start || pair.left_start;
    return [
      { start: pair.left_start, end: pair.left_start + pair.left_length - 1, kind: "left" },
      { start: rightStart, end: rightStart + pair.right_length - 1, kind: "right" },
    ];
  }, [normalizedSequence, selectedSummary]);

  const selectedAmpRange = useMemo((): { start: number; end: number } | null => {
    if (!selectedSummary || !normalizedSequence) return null;
    const pair = selectedSummary.pair;
    const ampStart = Math.max(1, Math.min(pair.left_start, pair.right_start || pair.left_start));
    let ampEnd: number;
    if (pair.product_size && pair.product_size > 0) {
      ampEnd = ampStart + pair.product_size - 1;
    } else {
      const leftEnd = pair.left_start + pair.left_length - 1;
      const rightStart = pair.right_start || pair.left_start;
      const rightEnd = rightStart + pair.right_length - 1;
      ampEnd = Math.max(leftEnd, rightEnd);
    }
    ampEnd = Math.min(ampEnd, normalizedSequence.length);
    return { start: ampStart, end: ampEnd };
  }, [normalizedSequence, selectedSummary]);

  return (
    <section className="seq-result-block">
      <h2 className="panel-title">PrimerBLAST（設計 + 複数DBスクリーニング）</h2>
      <p className="panel-hint">
        配列を貼り付け→Primer3 で候補を作成→ローカル BLAST DB（複数選択可）で左右プライマーを一括 BLASTし、予測 PCR 産物の本数で
        「使えそうなペア」を並べます。
      </p>

      <div className="form-grid">
        <label className="seq-label grid-span-2">
          テンプレート配列（FASTA / 生配列）:
          <textarea
            className="seq-textarea"
            rows={7}
            value={sequenceText}
            onChange={(e) => setSequenceText(e.target.value)}
            placeholder={"例:\n>target\nATGC..."}
            disabled={loading}
          />
          <div className="seq-hint">
            正規化後: {normalizedInputSequence.length.toLocaleString()} bp
          </div>
        </label>

        <label className="seq-label">
          産物長 min (bp):
          <input
            type="number"
            className="seq-input"
            value={requestedProductRange.min}
            min={1}
            onChange={(e) => setProductMin(Math.max(1, Number(e.target.value) || 1))}
            disabled={loading}
          />
        </label>
        <label className="seq-label">
          産物長 max (bp):
          <input
            type="number"
            className="seq-input"
            value={requestedProductRange.max}
            min={1}
            onChange={(e) => setProductMax(Math.max(1, Number(e.target.value) || 1))}
            disabled={loading}
          />
        </label>

        <label className="seq-label">
          候補数（Primer3）:
          <input
            type="number"
            className="seq-input"
            value={numReturn}
            onChange={(e) => setNumReturn(Math.max(1, Number(e.target.value) || 1))}
            min={1}
            max={200}
            disabled={loading}
          />
        </label>
        <label className="seq-label">
          スクリーニング上限（上位 N ペア）:
          <input
            type="number"
            className="seq-input"
            value={screenTopN}
            onChange={(e) => setScreenTopN(Math.max(1, Number(e.target.value) || 1))}
            min={1}
            max={200}
            disabled={loading}
          />
        </label>

        <div className="seq-label grid-span-2">
          <div className="blast-backend-row checklist-grid">
            <span>ローカル DB（複数選択可）:</span>
            {localDbOptions.map((opt) => (
              <label key={opt.value}>
                <input
                  type="checkbox"
                  checked={selectedLocalDbs.includes(opt.value)}
                  onChange={() => toggleLocalDb(opt.value)}
                  disabled={loading}
                />{" "}
                {opt.label}
              </label>
            ))}
          </div>
          <div className="primer-row">
            <input
              type="text"
              className="seq-input"
              placeholder="追加の makeblastdb prefix (任意)"
              value={customLocalDb}
              onChange={(e) => setCustomLocalDb(e.target.value)}
              disabled={loading}
            />
          </div>
          <div className="primer-row" style={{ alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
            <span className="tag-label">ローカルモード</span>
            <span className="seq-hint">CPU（通常）</span>
            <button type="button" className="seq-button secondary" onClick={resetSelectedLocalDbs} disabled={loading}>
              DB選択をリセット
            </button>
          </div>
          <div className="tag-row">
            <span className="tag-label">選択中</span>
            <code className="tag-db">
              {localDbPathsToQuery.length
                ? localDbPathsToQuery.map((p) => labelForDbPath(p, localDbOptions)).join(", ")
                : "-"}
            </code>
          </div>
          <span className="seq-hint">BLAST DB base: {DEFAULT_BLAST_DB_BASE}</span>
          {localDbLoading ? <span className="seq-hint">DB 取得中…</span> : null}
          {localDbError ? <p className="seq-error">DB取得エラー: {localDbError}</p> : null}
        </div>
      </div>

      <details className="ui-details" style={{ marginTop: "0.6rem" }}>
        <summary>詳細設定（Primer3 / BLAST）</summary>
        <div className="ui-details-body">
          <div className="form-grid">
            <label className="seq-label">
              Tm（opt）:
              <input
                type="number"
                className="seq-input"
                value={optTm}
                onChange={(e) => setOptTm(Number(e.target.value) || 0)}
                step="0.1"
                disabled={loading}
              />
            </label>
            <label className="seq-label">
              Tm（min）:
              <input
                type="number"
                className="seq-input"
                value={minTm}
                onChange={(e) => setMinTm(Number(e.target.value) || 0)}
                step="0.1"
                disabled={loading}
              />
            </label>
            <label className="seq-label">
              Tm（max）:
              <input
                type="number"
                className="seq-input"
                value={maxTm}
                onChange={(e) => setMaxTm(Number(e.target.value) || 0)}
                step="0.1"
                disabled={loading}
              />
            </label>
            <label className="seq-label">
              task:
              <input
                type="text"
                className="seq-input"
                value={blastTask}
                onChange={(e) => setBlastTask(e.target.value)}
                placeholder="blastn-short"
                disabled={loading}
              />
            </label>
            <label className="seq-label">
              E-value:
              <input
                type="number"
                className="seq-input"
                value={blastEvalue}
                onChange={(e) => setBlastEvalue(Number(e.target.value) || 0)}
                step="0.1"
                disabled={loading}
              />
            </label>
            <label className="seq-label">
              最大ヒット数:
              <input
                type="number"
                className="seq-input"
                value={blastMaxHits}
                onChange={(e) => setBlastMaxHits(Math.max(1, Number(e.target.value) || 1))}
                min={1}
                max={500}
                disabled={loading}
              />
            </label>
            <label className="seq-label">
              max hsps:
              <input
                type="number"
                className="seq-input"
                value={blastMaxHsps ?? ""}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setBlastMaxHsps(Number.isNaN(v) ? null : Math.max(1, v));
                }}
                placeholder="BLASTデフォルト"
                min={1}
                max={100}
                disabled={loading}
              />
            </label>
            <label className="seq-label">
              num_threads:
              <input
                type="number"
                className="seq-input"
                value={blastNumThreads ?? ""}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setBlastNumThreads(Number.isNaN(v) ? null : Math.max(1, v));
                }}
                placeholder="自動"
                min={1}
                max={64}
                disabled={loading}
              />
              <span className="seq-hint">未指定なら CPU に応じて自動</span>
            </label>
          </div>
        </div>
      </details>

      <div className="primer-row" style={{ marginTop: "0.8rem", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
        <button type="button" className="seq-button" onClick={() => void runPrimerBlast()} disabled={loading}>
          {loading ? "実行中..." : "PrimerBLAST を実行"}
        </button>
        <label className="seq-hint" style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
          <input
            type="checkbox"
            checked={filterNoOfftarget}
            onChange={(e) => setFilterNoOfftarget(e.target.checked)}
            disabled={loading}
          />
          どのDBでも予測産物が 2 本以上のペアを除外
        </label>
        <label className="seq-hint" style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
          <input
            type="checkbox"
            checked={filterRequireAllDbs}
            onChange={(e) => setFilterRequireAllDbs(e.target.checked)}
            disabled={loading}
          />
          全DBで予測産物が 1 本以上のペアのみ
        </label>
        {error ? <span className="seq-error">エラー: {error}</span> : null}
      </div>

      <JobProgressCard
        title="ローカル BLAST"
        jobId={blastJobId}
        job={blastJobInfo}
        onCancel={blastJobId ? cancelBlastJob : null}
        cancelDisabled={!blastJobId}
      />

      {templateLength > 0 && effectiveProductRange.max !== requestedProductRange.max ? (
        <div className="seq-hint" style={{ marginTop: "0.35rem" }}>
          テンプレート配列が短いため、産物長 max は {effectiveProductRange.max.toLocaleString()} bp として扱います。
        </div>
      ) : null}

      {designResult ? (
        <div className="seq-hint" style={{ marginTop: "0.6rem" }}>
          入力 {designResult.sequence_length.toLocaleString()} bp / Primer3 候補 {designResult.num_candidates.toLocaleString()} 件 /
          表示 {displayedSummaries.length.toLocaleString()} 件 / DB: {screenDbLabels.join(", ")}
        </div>
      ) : null}

      {displayedSummaries.length ? (
        <div className="table-scroll" style={{ marginTop: "0.6rem" }}>
          <table className="seq-table">
            <thead>
              <tr>
                <th>#</th>
                <th>判定</th>
                <th>産物長</th>
                <th>penalty</th>
                <th>Tm(L/R)</th>
                <th>Left</th>
                <th>Right</th>
                {screenDbLabels.map((db) => (
                  <th key={db}>{db}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayedSummaries.map((s, idx) => {
                const isActive = idx === selectedRow;
                const okAll = s.dbTooMany === 0 && s.dbMissing === 0 && screenDbLabels.length > 0;
                const noOff = s.dbTooMany === 0 && screenDbLabels.length > 0;
                const badge = okAll ? "OK" : noOff ? "候補" : "注意";
                const tm = `${s.pair.left_tm?.toFixed(1) ?? "-"} / ${s.pair.right_tm?.toFixed(1) ?? "-"}`;
                const penalty = s.pair.pair_penalty != null ? s.pair.pair_penalty.toFixed(2) : "-";
                const prodSize = s.pair.product_size != null ? s.pair.product_size : "-";
                return (
                  <tr
                    key={`${s.idx}-${s.leftSeq}-${s.rightSeq}`}
                    className={isActive ? "primer-row-selected" : undefined}
                    onClick={() => setSelectedRow(idx)}
                  >
                    <td>{idx + 1}</td>
                    <td>
                      <span className={`spec-tag ${okAll ? "good" : noOff ? "muted" : "warn"}`}>
                        {badge}
                      </span>
                    </td>
                    <td>{prodSize}</td>
                    <td>{penalty}</td>
                    <td>{tm}</td>
                    <td>{s.pair.left_sequence}</td>
                    <td>{s.pair.right_sequence}</td>
                    {screenDbLabels.map((db) => (
                      <td key={`${idx}-${db}`}>{s.perDbAmplicons[db] ?? 0}</td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {selectedSummary && normalizedSequence ? (
        <section className="seq-result-block" style={{ marginTop: "0.8rem" }}>
          <h3>選択中ペアの詳細</h3>
          <div className="seq-hint">
            local hits: {selectedSummary.leftHits.toLocaleString()} / {selectedSummary.rightHits.toLocaleString()} / 予測産物（{effectiveProductRange.min}–{effectiveProductRange.max} bp）:{" "}
            {selectedAmplicons.length.toLocaleString()} 本
          </div>
          {selectedAmpRange ? (
            <div style={{ marginTop: "0.5rem" }}>
              <FeatureSequenceView
                sequence={normalizedSequence}
                primerRanges={selectedPrimerRanges}
                highlightRange={selectedAmpRange}
                blockLen={60}
                fontSize="0.92rem"
              />
            </div>
          ) : null}

          {selectedAmplicons.length ? (
            <div className="table-scroll" style={{ marginTop: "0.6rem" }}>
              <table className="seq-table">
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
                  {selectedAmplicons.map((a, i) => (
                    <tr key={`${a.dbSource}-${a.subject}-${a.start}-${a.end}`}>
                      <td>{i + 1}</td>
                      <td>{dbLabelFromSource(a.dbSource)}</td>
                      <td>{a.subject}</td>
                      <td>{geneLabelForAmplicon(a)}</td>
                      <td>{a.start.toLocaleString()}</td>
                      <td>{a.end.toLocaleString()}</td>
                      <td>{a.length.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="seq-hint" style={{ marginTop: "0.6rem" }}>
              条件を満たす予測 PCR 産物は見つかりませんでした（ヒット位置が離れすぎている/同じコンティグに並んでいない/ヒットが少ない など）。
            </p>
          )}
        </section>
      ) : null}
    </section>
  );
};
