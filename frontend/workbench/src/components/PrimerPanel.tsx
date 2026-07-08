import React, { useMemo, useState } from "react";
import { bioapiClient } from "../api/bioapiClient";
import type { PrimerDesignResponse, PrimerPair } from "../types/primers";
import type { BlastResponse, BlastHit, NCBITarget } from "../types/blast";
import type { JobInfo } from "../types/jobs";
import { computePrimerAmplicons, countLocalHits } from "../utils/primerBlast";
import { downloadMarkdown, openPrintViewForMarkdown } from "../utils/exportReport";
import { runBlastBatchLocalJob } from "../utils/blastBatchLocalJob";
import { FeatureSequenceView, type PrimerRange1Based } from "./FeatureSequenceView";
import { JobProgressCard } from "./JobProgressCard";
import {
  DEFAULT_BLAST_DB_BASE,
  labelForDbPath,
  relabelLocalBlastHits,
  useLocalBlastDbOptions,
  usePreferredLocalDbPaths,
  normalizeLocalDbValue,
} from "../utils/localBlastDbs";
import { useLocalBlastMode } from "../utils/localBlastMode";

// プライマー設計用のシンプルな UI
export const PrimerPanel: React.FC = () => {
  const { options: localDbOptions } = useLocalBlastDbOptions();
  const [sequence, setSequence] = useState<string>("");
  const [productSizeRange, setProductSizeRange] = useState<string>("100-200");
  const [numReturn, setNumReturn] = useState<number>(3);
  const [subregionStart, setSubregionStart] = useState<number | null>(null);
  const [subregionEnd, setSubregionEnd] = useState<number | null>(null);

  // Tm 条件（デフォルトはバックエンド実装と同じ値）
  const [optTm, setOptTm] = useState<number>(60.0);
  const [minTm, setMinTm] = useState<number>(57.0);
  const [maxTm, setMaxTm] = useState<number>(63.0);

  // ターゲット領域（1-based, 任意）
  const [targetStart, setTargetStart] = useState<number | null>(null);
  const [targetLength, setTargetLength] = useState<number | null>(null);

  // 可視化用: 正規化済み配列と選択中のペア
  const [normalizedSequence, setNormalizedSequence] = useState<string>("");
  const [selectedPairIndex, setSelectedPairIndex] = useState<number | null>(
    null,
  );

  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PrimerDesignResponse | null>(null);
  const [selectedLocalDbs, setSelectedLocalDbs] = usePreferredLocalDbPaths();
  const [customLocalDb, setCustomLocalDb] = useState<string>("");
  const effectiveLocalDbs = useMemo(() => {
    const manual = normalizeLocalDbValue(customLocalDb);
    const list = [...selectedLocalDbs, ...(manual ? [manual] : [])];
    return Array.from(new Set(list)).filter(Boolean);
  }, [customLocalDb, selectedLocalDbs]);
  const [blastUseLocal, setBlastUseLocal] = useState<boolean>(true);
  const [blastUseNcbi, setBlastUseNcbi] = useState<boolean>(false);
  const [localMode, setLocalMode] = useLocalBlastMode();
  const [ncbiTargets, setNcbiTargets] = useState<NCBITarget[]>([
    { label: "user_target", entrez_query: "", database: "nt" },
  ]);
  const [blastMaxHits, setBlastMaxHits] = useState<number>(5);
  const [blastTask, setBlastTask] = useState<string>("blastn-short");
  const [blastEvalue, setBlastEvalue] = useState<number>(1e-5);
  const [blastMaxHsps, setBlastMaxHsps] = useState<number | null>(null);
  const [blastNumThreads, setBlastNumThreads] = useState<number | null>(null);
  const [ncbiDb, setNcbiDb] = useState<string>("nt");
  const [ncbiQuery, setNcbiQuery] = useState<string>(
    "",
  );
  const [blastLoading, setBlastLoading] = useState<boolean>(false);
  const [blastError, setBlastError] = useState<string | null>(null);
  const [blastLeft, setBlastLeft] = useState<BlastResponse | null>(null);
  const [blastRight, setBlastRight] = useState<BlastResponse | null>(null);
  const [blastJobId, setBlastJobId] = useState<string | null>(null);
  const [blastJobInfo, setBlastJobInfo] = useState<JobInfo | null>(null);
  const [screening, setScreening] = useState<boolean>(false);
  const [screenStatus, setScreenStatus] = useState<{ done: number; total: number } | null>(null);
  const [screenResults, setScreenResults] = useState<
    Record<number, { left: BlastResponse; right: BlastResponse }>
  >({});
  const [showUniqueOnly, setShowUniqueOnly] = useState<boolean>(false);

  // Primer3 詳細パラメータ（普段は隠す）
  const [showPrimer3Advanced, setShowPrimer3Advanced] = useState<boolean>(false);
  const [primerMinSize, setPrimerMinSize] = useState<number>(18);
  const [primerOptSize, setPrimerOptSize] = useState<number>(20);
  const [primerMaxSize, setPrimerMaxSize] = useState<number>(27);
  const [primerMinGc, setPrimerMinGc] = useState<number>(20.0);
  const [primerMaxGc, setPrimerMaxGc] = useState<number>(80.0);
  const [primerSaltMonovalent, setPrimerSaltMonovalent] = useState<number>(50.0);
  const [primerDnaConc, setPrimerDnaConc] = useState<number>(50.0);

  const displayCandidates = useMemo(() => {
    if (!result) return [];
    const list = result.candidates.map((pair, idx) => ({ pair, idx }));
    if (!showUniqueOnly) return list;
    return list.filter(({ idx }) => {
      const res = screenResults[idx];
      if (!res) return false;
      const { amplicons } = computePrimerAmplicons(res.left, res.right);
      return amplicons.length === 1;
    });
  }, [result, screenResults, showUniqueOnly]);

  const specificityLabel = (idx: number): { text: string; className: string } => {
    const res = screenResults[idx];
    if (!res) return { text: "未実行", className: "spec-tag muted" };
    const { amplicons } = computePrimerAmplicons(res.left, res.right);
    const ampCount = amplicons.length;
    const lLocal = countLocalHits(res.left);
    const rLocal = countLocalHits(res.right);

    if (ampCount === 1) {
      return { text: "OK:amp=1（ローカル）", className: "spec-tag good" };
    }
    if (ampCount === 0 && lLocal === 0 && rLocal === 0) {
      return { text: "ローカルヒット 0/0", className: "spec-tag warn" };
    }
    if (ampCount === 0) {
      return {
        text: `amp=0（local hits ${lLocal}/${rLocal})`,
        className: "spec-tag warn",
      };
    }
    return {
      text: `多重 amp=${ampCount}（local ${lLocal}/${rLocal})`,
      className: "spec-tag warn",
    };
  };

  const buildMarkdownReport = (): string => {
    if (!result) return "";
    const dt = new Date();
    const lines: string[] = [];
    lines.push("# 一般プライマー設計レポート");
    lines.push("");
    lines.push(`- 作成時刻: ${dt.toLocaleString()}`);
    lines.push(`- テンプレート配列長: ${result.sequence_length} bp`);
    lines.push(
      `- product size range: \`${productSizeRange || "-"}\` ／ num_return: \`${numReturn}\``,
    );
    if (targetStart && targetLength) {
      lines.push(
        `- ターゲット: start=\`${targetStart}\`, length=\`${targetLength}\``,
      );
    }
    if (subregionStart && subregionEnd) {
      lines.push(
        `- サブ領域: ${subregionStart}–${subregionEnd} bp (長さ ${
          subregionEnd - subregionStart + 1
        } bp)`,
      );
    }
    lines.push(
      `- Tm 条件: 最適 \`${optTm}℃\` ／ 最小 \`${minTm}℃\` ／ 最大 \`${maxTm}℃\``,
    );
    lines.push("");

    lines.push("## 設計結果");
    lines.push("");
    lines.push(
      "| # | 左プライマー | 右プライマー | 産物長 (bp) | Primer3 penalty | 左 Tm (℃) | 右 Tm (℃) |",
    );
    lines.push("| ---: | --- | --- | ---: | ---: | ---: | ---: |");

    result.candidates.forEach((pair, idx) => {
      lines.push(
        `| ${idx + 1} | \`${pair.left_sequence}\` | \`${pair.right_sequence}\` | ${
          pair.product_size ?? ""
        } | ${
          typeof pair.pair_penalty === "number"
            ? pair.pair_penalty.toFixed(2)
            : ""
        } | ${pair.left_tm?.toFixed(1) ?? ""} | ${
          pair.right_tm?.toFixed(1) ?? ""
        } |`,
      );
    });

    lines.push("");
    lines.push("## 入力配列");
    lines.push("");
    const normalized = sequence.replace(/\s+/g, "").toUpperCase();
    lines.push("```");
    lines.push(normalized || "(空)");
    lines.push("```");
    lines.push("");

    return lines.join("\n");
  };

  const labelForDb = (path: string) =>
    labelForDbPath(path, localDbOptions);

  const toggleLocalDb = (path: string) => {
    setSelectedLocalDbs((prev) =>
      prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path],
    );
  };

  const cancelBlastJob = async () => {
    if (!blastJobId) return;
    try {
      const updated = await bioapiClient.cancelJob(blastJobId);
      setBlastJobInfo(updated);
    } catch (e) {
      setBlastError(e instanceof Error ? e.message : "ジョブのキャンセルに失敗しました。");
    }
  };

  const runLocalBlastJob = async (seqs: string[]): Promise<BlastResponse[]> => {
    if (!blastUseLocal) {
      return seqs.map(() => ({ num_hits: 0, hits: [] }));
    }

    const { result: batch } = await runBlastBatchLocalJob(
      {
        sequences: seqs,
        dbs: effectiveLocalDbs,
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

    if (!batch.results || batch.results.length !== seqs.length) {
      throw new Error("ローカル BLAST の結果件数が期待値と一致しませんでした。");
    }

    return batch.results.map((merged) => {
      const hits = relabelLocalBlastHits(merged?.hits ?? [], effectiveLocalDbs, localDbOptions);
      return { num_hits: hits.length, hits };
    });
  };

  const runNcbiBlast = async (seq: string): Promise<BlastResponse> => {
    if (!blastUseNcbi) return { num_hits: 0, hits: [] };
    const res = await bioapiClient.runBlastMulti({
      sequence: seq,
      db: effectiveLocalDbs[0] ?? "",
      max_target_seqs: blastMaxHits,
      max_hsps: blastMaxHsps ?? undefined,
      task: "blastn",
      evalue: blastEvalue,
      num_threads: blastNumThreads ?? undefined,
      backends: ["ncbi"],
      ncbi_database: ncbiDb,
      ncbi_entrez_query: ncbiQuery.trim() || undefined,
      ncbi_targets: ncbiTargets,
    });
    const hits = (res.hits ?? []).map((h) => ({ ...h, source: h.source || "ncbi" }));
    return { num_hits: hits.length, hits };
  };

  const summarizeResponse = (label: string, res: BlastResponse | null) => {
    if (!res) return [];
    const grouped = res.hits.reduce<Record<string, BlastHit[]>>((acc, h) => {
      const src = h.source || label;
      acc[src] = acc[src] || [];
      acc[src].push(h);
      return acc;
    }, {});
    return Object.entries(grouped).map(([src, hits]) => ({
      name: `${label} (${src})`,
      count: hits.length,
      topId: hits[0] ? hits[0].sseqid.split(/\s+/)[0] : "-",
      topPident: hits[0] ? hits[0].pident.toFixed(1) : "-",
      topE: hits[0] ? hits[0].evalue.toExponential(2) : "-",
    }));
  };

  const handleDesign = async () => {
    const trimmed = sequence.trim();
    if (!trimmed) {
      setError("プライマーを設計したい DNA 配列を入力してください。");
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);
    setSelectedPairIndex(null);
    setBlastLeft(null);
    setBlastRight(null);
    setBlastError(null);

    try {
      const body = {
        sequence: trimmed,
        num_return: numReturn,
        product_size_range: productSizeRange || null,
        target_start:
          subregionStart && subregionEnd
            ? subregionStart
            : targetStart,
        target_length:
          subregionStart && subregionEnd
            ? Math.max(1, subregionEnd - subregionStart + 1)
            : targetLength,
        opt_tm: optTm,
        min_tm: minTm,
        max_tm: maxTm,
        primer_min_size: primerMinSize,
        primer_opt_size: primerOptSize,
        primer_max_size: primerMaxSize,
        primer_min_gc: primerMinGc,
        primer_max_gc: primerMaxGc,
        primer_salt_monovalent: primerSaltMonovalent,
        primer_dna_conc: primerDnaConc,
      } as const;

      const res = await bioapiClient.designPrimers(body);
      const normalized = trimmed.replace(/\s+/g, "").toUpperCase();
      setNormalizedSequence(normalized);
      setSelectedPairIndex(res.candidates.length > 0 ? 0 : null);
      setResult(res);
    } catch (e) {
      const msg =
        e instanceof Error
          ? e.message
          : "プライマー設計中に思わぬエラーが起きました。";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const renderPairRow = (pair: PrimerPair, index: number) => (
    <tr
      key={pair.index ?? index}
      className={
        selectedPairIndex === index ? "primer-row-selected" : undefined
      }
      onClick={() => setSelectedPairIndex(index)}
    >
      <td>{index + 1}</td>
      <td>{pair.left_sequence}</td>
      <td>{pair.right_sequence}</td>
      <td>{pair.product_size ?? "-"}</td>
      <td>{pair.pair_penalty?.toFixed(2) ?? "-"}</td>
      <td>{pair.left_tm?.toFixed(1) ?? "-"}</td>
      <td>{pair.right_tm?.toFixed(1) ?? "-"}</td>
    </tr>
  );

  const renderAmpliconView = () => {
    if (!result || selectedPairIndex === null) {
      return null;
    }
    const pair = result.candidates[selectedPairIndex];
    if (!pair || !normalizedSequence) {
      return null;
    }

    const seq = normalizedSequence;
    const length = seq.length;

    // 増幅領域の開始位置（1-based）
    const ampStart = Math.max(
      1,
      Math.min(pair.left_start, pair.right_start || pair.left_start),
    );

    // product_size があればそれを優先して終端を決める
    let ampEnd: number;
    if (pair.product_size && pair.product_size > 0) {
      ampEnd = ampStart + pair.product_size - 1;
    } else {
      const leftEnd = pair.left_start + pair.left_length - 1;
      const rightEnd = pair.right_start + pair.right_length - 1;
      ampEnd = Math.max(leftEnd, rightEnd);
    }
    ampEnd = Math.min(ampEnd, length);
    const primerRanges: PrimerRange1Based[] = [
      { start: pair.left_start, end: pair.left_start + pair.left_length - 1, kind: "left" },
      {
        start: pair.right_start ?? pair.left_start,
        end: (pair.right_start ?? pair.left_start) + pair.right_length - 1,
        kind: "right",
      },
    ];

    return (
      <div className="amplicon-viewer">
        <p className="amplicon-title">
          選択中ペアの増幅領域（{ampStart}–{ampEnd} bp, 長さ{" "}
          {ampEnd - ampStart + 1} bp）
        </p>
        <FeatureSequenceView
          sequence={seq}
          primerRanges={primerRanges}
          highlightRange={{ start: ampStart, end: ampEnd }}
          blockLen={60}
          fontSize="0.92rem"
        />
      </div>
    );
  };

  return (
    <section className="seq-result-block">
      <h2 className="panel-title">プライマー設計（Primer3）</h2>
      <p className="panel-hint">
        product size range / Tm 条件 / target (1-based) を指定して設計します。テーブル行をクリックすると増幅領域をハイライト表示します。ローカル BLAST DB は選択式で、任意パスは「手動入力」から追加できます。
      </p>
      <div className="primer-row" style={{ marginBottom: "0.4rem" }}>
        <button
          type="button"
          className="seq-button secondary"
          onClick={() => {
            const md = buildMarkdownReport();
            if (!md) return;
            downloadMarkdown(md, "primer_design");
          }}
          disabled={!result}
        >
          結果を Markdown として保存
        </button>
        <button
          type="button"
          className="seq-button secondary"
          onClick={() => {
            const md = buildMarkdownReport();
            if (!md) return;
            openPrintViewForMarkdown(md, "プライマー設計レポート");
          }}
          disabled={!result}
        >
          印刷用ビューを開く（PDF 保存に利用）
        </button>
      </div>
      <div className="primer-grid">
        <div className="primer-controls">
          <label className="seq-label">
            テンプレート配列（DNA）:
            <textarea
              className="seq-textarea"
              rows={6}
              placeholder="例: ATGCGT..."
              value={sequence}
              onChange={(e) => setSequence(e.target.value)}
            />
          </label>

          <div className="primer-row">
            <label className="seq-label">
              増幅させたい産物サイズ（product size range）:
              <input
                type="text"
                className="seq-input"
                value={productSizeRange}
                onChange={(e) => setProductSizeRange(e.target.value)}
                placeholder='例: "100-200" または "100-200 300-400"'
              />
            </label>

            <label className="seq-label">
              候補数（何組ほしいか）:
              <input
                type="number"
                className="seq-input"
                min={1}
                max={20}
                value={numReturn}
                onChange={(e) =>
                  setNumReturn(
                    Number.isNaN(Number(e.target.value))
                      ? 1
                      : Number(e.target.value),
                  )
                }
              />
            </label>
          </div>

          <div className="primer-row">
            <button
              type="button"
              className="seq-button secondary"
              onClick={() => setShowPrimer3Advanced((v) => !v)}
            >
              {showPrimer3Advanced ? "Primer3 詳細を隠す" : "Primer3 詳細パラメータを表示"}
            </button>
          </div>
          {showPrimer3Advanced && (
            <div
              className="primer-row"
              style={{
                background: "#f9fafb",
                border: "1px dashed #9ca3af",
                borderRadius: "4px",
                padding: "0.6rem",
                flexWrap: "wrap",
                gap: "0.75rem",
              }}
            >
              <label className="seq-label">
                プライマー長 min/opt/max
                <div style={{ display: "flex", gap: "0.3rem" }}>
                  <input
                    type="number"
                    className="seq-input"
                    style={{ width: "70px" }}
                    value={primerMinSize}
                    onChange={(e) => setPrimerMinSize(Number(e.target.value) || 18)}
                  />
                  <input
                    type="number"
                    className="seq-input"
                    style={{ width: "70px" }}
                    value={primerOptSize}
                    onChange={(e) => setPrimerOptSize(Number(e.target.value) || 20)}
                  />
                  <input
                    type="number"
                    className="seq-input"
                    style={{ width: "70px" }}
                    value={primerMaxSize}
                    onChange={(e) => setPrimerMaxSize(Number(e.target.value) || 27)}
                  />
                </div>
              </label>
              <label className="seq-label">
                GC% min/max
                <div style={{ display: "flex", gap: "0.3rem" }}>
                  <input
                    type="number"
                    className="seq-input"
                    style={{ width: "70px" }}
                    value={primerMinGc}
                    onChange={(e) => setPrimerMinGc(Number(e.target.value) || 20)}
                  />
                  <input
                    type="number"
                    className="seq-input"
                    style={{ width: "70px" }}
                    value={primerMaxGc}
                    onChange={(e) => setPrimerMaxGc(Number(e.target.value) || 80)}
                  />
                </div>
              </label>
              <label className="seq-label">
                Salt (mM)
                <input
                  type="number"
                  className="seq-input"
                  style={{ width: "90px" }}
                  value={primerSaltMonovalent}
                  onChange={(e) =>
                    setPrimerSaltMonovalent(Number(e.target.value) || 50)
                  }
                />
              </label>
              <label className="seq-label">
                DNA 濃度 (nM)
                <input
                  type="number"
                  className="seq-input"
                  style={{ width: "90px" }}
                  value={primerDnaConc}
                  onChange={(e) => setPrimerDnaConc(Number(e.target.value) || 50)}
                />
              </label>
              <span className="seq-hint">
                Primer3 にそのまま渡すパラメータです。未指定に戻すには 18/20/27, 20–80%, 50/50 を推奨。
              </span>
            </div>
          )}

          <div className="primer-row">
            <label className="seq-label">
              最適 Tm（℃）:
              <input
                type="number"
                className="seq-input"
                value={optTm}
                step={0.1}
                onChange={(e) => setOptTm(Number(e.target.value) || 0)}
              />
            </label>
            <label className="seq-label">
              最小 / 最大 Tm（℃）:
              <div style={{ display: "flex", gap: "0.4rem" }}>
                <input
                  type="number"
                  className="seq-input"
                  value={minTm}
                  step={0.1}
                  onChange={(e) => setMinTm(Number(e.target.value) || 0)}
                  placeholder="min"
                />
                <input
                  type="number"
                  className="seq-input"
                  value={maxTm}
                  step={0.1}
                  onChange={(e) => setMaxTm(Number(e.target.value) || 0)}
                  placeholder="max"
                />
              </div>
            </label>
          </div>

          <div className="primer-row">
            <label className="seq-label">
              ターゲット開始位置（1-based, 任意）:
              <input
                type="number"
                className="seq-input"
                min={1}
                value={targetStart ?? ""}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setTargetStart(Number.isNaN(v) ? null : v);
                }}
                placeholder="例: 150"
              />
            </label>
            <label className="seq-label">
              ターゲット長（bp, 任意）:
              <input
                type="number"
                className="seq-input"
                min={1}
                value={targetLength ?? ""}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setTargetLength(Number.isNaN(v) ? null : v);
                }}
                placeholder="例: 200"
              />
            </label>
          </div>
          <div className="primer-row">
            <label className="seq-label">
              選択サブ領域の開始:
              <input
                type="number"
                className="seq-input"
                min={1}
                value={subregionStart ?? ""}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setSubregionStart(Number.isNaN(v) ? null : v);
                }}
                placeholder="例: 200"
              />
            </label>
            <label className="seq-label">
              選択サブ領域の終了:
              <input
                type="number"
                className="seq-input"
                min={1}
                value={subregionEnd ?? ""}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setSubregionEnd(Number.isNaN(v) ? null : v);
                }}
                placeholder="例: 400"
              />
            </label>
          </div>

          <button
            type="button"
            className="seq-button"
            onClick={handleDesign}
            disabled={loading}
          >
            {loading ? "設計中..." : "この配列でプライマーを設計する"}
          </button>

          {error && <p className="seq-error">エラー: {error}</p>}
        </div>

        <div className="primer-results">
          {result && (
            <>
              <div className="primer-result-summary">
                <p>
                  入力シーケンス長: {result.sequence_length} bp ／
                  見つかった候補数: {result.num_candidates}
                </p>
                <div className="primer-screen-controls">
                  <div className="primer-screen-row">
                    <span className="tag-label">ローカル DB</span>
                    <code className="tag-db">
                      {effectiveLocalDbs.length ? effectiveLocalDbs.join(", ") : "-"}
                    </code>
                  </div>
                  <div className="primer-screen-row">
                    <label>
                      <input
                        type="checkbox"
                        checked={showUniqueOnly}
                        onChange={(e) => setShowUniqueOnly(e.target.checked)}
                      />{" "}
                      左右ともヒット 1 本のみのペアだけ表示
                    </label>
                  </div>
                  <button
                    type="button"
                    className="seq-button"
                    onClick={async () => {
                      if (!result || result.candidates.length === 0) {
                        setBlastError("設計結果がありません。");
                        return;
                      }
                      if (!blastUseLocal) {
                        setBlastError(
                          "スクリーニングはローカル DB のヒット（amplicon 推定）に基づくため、ローカル BLAST+ を有効にしてください。",
                        );
                        return;
                      }
                      if (effectiveLocalDbs.length === 0) {
                        setBlastError("ローカル BLAST+ を使う場合、DB を選択してください。");
                        return;
                      }
                      setScreening(true);
                      setBlastError(null);
                      setScreenStatus({ done: 0, total: result.candidates.length });
                      const next: Record<
                        number,
                        { left: BlastResponse; right: BlastResponse }
                      > = {};
                      try {
                        const uniqPrimerSeqs: string[] = [];
                        const idxBySeq = new Map<string, number>();
                        const indexOf = (raw: string): string => {
                          const key = raw.replace(/\s+/g, "").toUpperCase();
                          if (!key) return "";
                          if (idxBySeq.has(key)) return key;
                          idxBySeq.set(key, uniqPrimerSeqs.length);
                          uniqPrimerSeqs.push(key);
                          return key;
                        };

                        const pairRefs = result.candidates
                          .map((pair, idx) => ({
                            idx,
                            left: indexOf(pair.left_sequence),
                            right: indexOf(pair.right_sequence),
                          }))
                          .filter((x) => x.left && x.right);

                        const localResults = await runLocalBlastJob(uniqPrimerSeqs);
                        const blastMap = new Map<string, BlastResponse>();
                        for (let i = 0; i < uniqPrimerSeqs.length; i += 1) {
                          const seq = uniqPrimerSeqs[i];
                          blastMap.set(seq, localResults[i]);
                        }

                        for (let i = 0; i < pairRefs.length; i += 1) {
                          const ref = pairRefs[i];
                          const leftRes = blastMap.get(ref.left) ?? { num_hits: 0, hits: [] };
                          const rightRes = blastMap.get(ref.right) ?? { num_hits: 0, hits: [] };
                          next[ref.idx] = { left: leftRes, right: rightRes };
                          setScreenStatus({ done: i + 1, total: pairRefs.length });
                        }
                        setScreenResults(next);
                      } catch (e) {
                        const msg =
                          e instanceof Error
                            ? e.message
                            : "プライマー一括 BLAST 中にエラーが発生しました。";
                        setBlastError(msg);
                      } finally {
                        setScreening(false);
                        setScreenStatus(null);
                      }
                    }}
                    disabled={screening}
                  >
                    {screening ? "スクリーニング中..." : "全候補を BLAST でスクリーニング"}
                  </button>
                  <JobProgressCard
                    title="ローカル BLAST"
                    jobId={blastJobId}
                    job={blastJobInfo}
                    onCancel={blastJobId ? cancelBlastJob : null}
                    cancelDisabled={!blastJobId}
                  />
                  {screenStatus && (
                    <p className="seq-hint" style={{ marginBottom: 0 }}>
                      スクリーニング: {screenStatus.done}/{screenStatus.total}（
                      {screenStatus.total > 0
                        ? ((screenStatus.done / screenStatus.total) * 100).toFixed(0)
                        : "0"}
                      %）
                    </p>
                  )}
                </div>
              </div>
              <div className="table-scroll">
                <PrimerResultsTable
                  result={result}
                  displayCandidates={displayCandidates}
                  selectedPairIndex={selectedPairIndex}
                  onSelect={setSelectedPairIndex}
                  specificityLabel={specificityLabel}
                />
              </div>
              {renderAmpliconView()}
              {result.candidates.length > 0 && (
                <div className="primer-blast-block">
                  <div className="primer-blast-header">
                    <div>
                      <h3>Primer-BLAST 相当（選択ペアの左右を個別 BLAST）</h3>
                      <p className="panel-hint">
                        ローカル DB を使って、選択中の左・右プライマー配列を簡易 BLAST します。
                      </p>
                    </div>
                    <div className="primer-blast-actions">
                      <div className="seq-label">
                        <div className="blast-backend-row checklist-grid">
                          <span>ローカル DB（複数選択可）:</span>
                          {localDbOptions.map((opt) => (
                            <label key={opt.value}>
                              <input
                                type="checkbox"
                                checked={selectedLocalDbs.includes(opt.value)}
                                onChange={() => toggleLocalDb(opt.value)}
                                disabled={blastLoading}
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
                            disabled={blastLoading}
                            placeholder="追加の makeblastdb prefix (任意)"
                          />
                        </div>
                        <div className="tag-row">
                          <span className="tag-label">選択中</span>
                          <code className="tag-db">
                            {effectiveLocalDbs.length ? effectiveLocalDbs.join(", ") : "-"}
                          </code>
                        </div>
                        <span className="seq-hint">
                          BLAST DB base: {DEFAULT_BLAST_DB_BASE} ／ num_threads:{" "}
                          {blastNumThreads != null ? blastNumThreads : "自動 (CPU に応じて最大24、複数DBは自動で割り当て)"}
                        </span>
                      </div>
                      <label className="seq-label">
                        実行先:
                        <div className="blast-backend-row checklist-grid">
                          <label>
                            <input
                              type="checkbox"
                              checked={blastUseLocal}
                              onChange={(e) => setBlastUseLocal(e.target.checked)}
                              disabled={blastLoading}
                            />{" "}
                            ローカル BLAST+
                          </label>
                        </div>
                      </label>
                      <label className="seq-label">
                        最大ヒット数:
                        <input
                          type="number"
                          className="seq-input"
                          min={1}
                          max={50}
                          value={blastMaxHits}
                          onChange={(e) => {
                            const v = Number(e.target.value);
                            setBlastMaxHits(Number.isNaN(v) ? 5 : v);
                          }}
                          disabled={blastLoading}
                        />
                      </label>
                      <div className="primer-row" style={{ gap: "0.75rem", flexWrap: "wrap" }}>
                        <label className="seq-label" style={{ maxWidth: "180px" }}>
                          task:
                        <select
                          className="seq-input"
                          value={blastTask}
                          onChange={(e) => setBlastTask(e.target.value)}
                          disabled={blastLoading}
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
                            onChange={(e) => setBlastEvalue(Number(e.target.value) || 1e-5)}
                            disabled={blastLoading}
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
                            disabled={blastLoading}
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
                            disabled={blastLoading}
                          />
                          <span className="seq-hint">未指定なら CPU に応じて自動</span>
                        </label>
                        <label className="seq-label" style={{ maxWidth: "200px" }}>
                          local mode:
                          <span className="seq-hint">CPU（通常）</span>
                        </label>
                      </div>
                      <button
                        type="button"
                        className="seq-button"
                        onClick={async () => {
                          if (selectedPairIndex === null) {
                            setBlastError("BLAST するペアをテーブルで選択してください。");
                            return;
                          }
                          const pair = result.candidates[selectedPairIndex];
                          if (!pair?.left_sequence || !pair?.right_sequence) {
                            setBlastError("プライマー配列が取得できませんでした。");
                            return;
                          }
                          if (!blastUseLocal && !blastUseNcbi) {
                            setBlastError("少なくとも 1 つは BLAST 実行先を選んでください。");
                            return;
                          }
                          if (blastUseLocal && effectiveLocalDbs.length === 0) {
                            setBlastError("ローカル BLAST+ を使う場合、DB を選択してください。");
                            return;
                          }
                          setBlastLoading(true);
                          setBlastError(null);
                          setBlastLeft(null);
                          setBlastRight(null);
                          try {
                            const [leftLocal, rightLocal] = blastUseLocal
                              ? await runLocalBlastJob([pair.left_sequence, pair.right_sequence])
                              : [{ num_hits: 0, hits: [] }, { num_hits: 0, hits: [] }];

                            const [leftNcbi, rightNcbi] = blastUseNcbi
                              ? await Promise.all([
                                  runNcbiBlast(pair.left_sequence),
                                  runNcbiBlast(pair.right_sequence),
                                ])
                              : [{ num_hits: 0, hits: [] }, { num_hits: 0, hits: [] }];

                            const leftHits = [...(leftLocal.hits ?? []), ...(leftNcbi.hits ?? [])];
                            const rightHits = [...(rightLocal.hits ?? []), ...(rightNcbi.hits ?? [])];
                            setBlastLeft({ num_hits: leftHits.length, hits: leftHits });
                            setBlastRight({ num_hits: rightHits.length, hits: rightHits });
                          } catch (e) {
                            const msg =
                              e instanceof Error
                                ? e.message
                                : "Primer BLAST 実行中にエラーが発生しました。";
                            setBlastError(msg);
                          } finally {
                            setBlastLoading(false);
                          }
                        }}
                        disabled={blastLoading}
                      >
                        {blastLoading ? "BLAST 実行中..." : "選択ペアを BLAST"}
                      </button>
                      {blastUseLocal && blastLoading ? (
                        <JobProgressCard
                          title="ローカル BLAST"
                          jobId={blastJobId}
                          job={blastJobInfo}
                          onCancel={blastJobId ? cancelBlastJob : null}
                          cancelDisabled={!blastJobId}
                        />
                      ) : null}
                      {blastError && <p className="seq-error">エラー: {blastError}</p>}
                    </div>
                  </div>
              <div className="primer-blast-results">
                {(blastLeft || blastRight) && (
                  <div className="table-scroll" style={{ marginBottom: "0.5rem" }}>
                    <table className="seq-table">
                      <thead>
                        <tr>
                          <th>側</th>
                          <th>source</th>
                          <th>ヒット数</th>
                          <th>Top ID</th>
                          <th>%id</th>
                          <th>E-value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {summarizeResponse("Left", blastLeft).map((r) => (
                          <tr key={`L-${r.name}`}>
                            <td>Left</td>
                            <td>{r.name}</td>
                            <td>{r.count}</td>
                            <td>{r.topId}</td>
                            <td>{r.topPident}</td>
                            <td>{r.topE}</td>
                          </tr>
                        ))}
                        {summarizeResponse("Right", blastRight).map((r) => (
                          <tr key={`R-${r.name}`}>
                            <td>Right</td>
                            <td>{r.name}</td>
                            <td>{r.count}</td>
                            <td>{r.topId}</td>
                            <td>{r.topPident}</td>
                            <td>{r.topE}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {blastLeft && (
                  <div className="primer-blast-table">
                    <h4>Left primer ({blastLeft.num_hits} hits)</h4>
                    <PrimerBlastTable response={blastLeft} />
                  </div>
                    )}
                    {blastRight && (
                      <div className="primer-blast-table">
                        <h4>Right primer ({blastRight.num_hits} hits)</h4>
                        <PrimerBlastTable response={blastRight} />
                      </div>
                    )}
                    {!blastLeft && !blastRight && !blastLoading && (
                      <p className="seq-hint">
                        選択中のプライマーペアに対して簡易 BLAST を実行できます。複数ヒットがあれば特異性を再検討してください。
                      </p>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
          {!result && !loading && !error && (
            <p className="seq-hint">
              テンプレート配列と product size range を入力し、「プライマーを設計する」を押すと候補が表示されます。
            </p>
          )}
        </div>
      </div>
    </section>
  );
};

const PrimerResultsTable: React.FC<{
  result: PrimerDesignResponse;
  displayCandidates: { pair: PrimerPair; idx: number }[];
  selectedPairIndex: number | null;
  onSelect: (idx: number) => void;
  specificityLabel: (idx: number) => { text: string; className: string };
}> = ({ result, displayCandidates, selectedPairIndex, onSelect, specificityLabel }) => {
  return (
    <table className="seq-table">
      <thead>
        <tr>
          <th>#</th>
          <th>左プライマー (5&apos;→3&apos;)</th>
          <th>右プライマー (5&apos;→3&apos;)</th>
          <th>産物長 (bp)</th>
          <th>ペナルティ</th>
          <th>左 Tm (℃)</th>
          <th>右 Tm (℃)</th>
          <th>特異性</th>
        </tr>
      </thead>
      <tbody>
        {result.candidates.length === 0 ? (
          <tr>
            <td colSpan={8}>条件に合致するプライマー候補がありません。</td>
          </tr>
        ) : displayCandidates.length === 0 ? (
          <tr>
            <td colSpan={8}>特異性フィルターに合致する候補がありません。</td>
          </tr>
        ) : (
          displayCandidates.map(({ pair, idx }, displayIdx) => {
            const spec = specificityLabel(idx);
            return (
              <tr
                key={pair.index ?? idx}
                className={selectedPairIndex === idx ? "primer-row-selected" : undefined}
                onClick={() => onSelect(idx)}
              >
                <td>{displayIdx + 1}</td>
                <td>{pair.left_sequence}</td>
                <td>{pair.right_sequence}</td>
                <td>{pair.product_size ?? "-"}</td>
                <td>{pair.pair_penalty?.toFixed(2) ?? "-"}</td>
                <td>{pair.left_tm?.toFixed(1) ?? "-"}</td>
                <td>{pair.right_tm?.toFixed(1) ?? "-"}</td>
                <td>
                  <span className={spec.className}>{spec.text}</span>
                </td>
              </tr>
            );
          })
        )}
      </tbody>
    </table>
  );
};

const PrimerBlastTable: React.FC<{ response: BlastResponse }> = ({ response }) => {
  const splitIdAndDesc = (sseqid: string): { id: string; desc: string } => {
    const trimmed = sseqid.trim();
    if (!trimmed) return { id: "-", desc: "" };
    const parts = trimmed.split(/\s+/);
    return { id: parts[0] ?? "-", desc: parts.slice(1).join(" ") };
  };

  if (response.hits.length === 0) {
    return <p className="seq-hint">ヒットなし</p>;
  }

  return (
    <div className="table-scroll">
      <table className="seq-table">
        <thead>
          <tr>
            <th>#</th>
            <th>ヒット ID</th>
            <th>% identity</th>
            <th>長さ</th>
            <th>E-value</th>
            <th>範囲</th>
            <th>source</th>
          </tr>
        </thead>
        <tbody>
          {response.hits.map((hit: BlastHit, idx: number) => {
            const { id, desc } = splitIdAndDesc(hit.sseqid);
            return (
              <tr key={`${hit.sseqid}-${idx}`}>
                <td>{idx + 1}</td>
                <td>
                  <span className="blast-id">{id}</span>
                  <div className="blast-desc">{desc}</div>
                </td>
                <td>{hit.pident.toFixed(1)}</td>
                <td>{hit.length}</td>
                <td>{hit.evalue.toExponential(2)}</td>
                <td>
                  {hit.qstart}–{hit.qend}
                </td>
                <td>{hit.source ?? "-"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};
