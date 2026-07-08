import React, { useMemo } from "react";

export type Range1Based = { start: number; end: number };
export type PrimerRange1Based = Range1Based & { kind: "left" | "right" };

type Range0 = { start0: number; end0: number };

const clampRanges0 = (ranges: Range1Based[], seqLen: number): Range0[] => {
  const out: Range0[] = [];
  ranges.forEach((r) => {
    const s = Number(r.start);
    const e = Number(r.end);
    if (!Number.isFinite(s) || !Number.isFinite(e)) return;
    const left1 = Math.max(1, Math.min(s, e));
    const right1 = Math.min(seqLen, Math.max(s, e));
    if (right1 < left1) return;
    out.push({ start0: left1 - 1, end0: right1 - 1 });
  });
  return out;
};

const buildMask = (ranges0: Range0[], seqLen: number): Uint8Array => {
  const mask = new Uint8Array(seqLen);
  ranges0.forEach((r) => {
    const s = Math.max(0, Math.min(seqLen - 1, r.start0));
    const e = Math.max(0, Math.min(seqLen - 1, r.end0));
    for (let i = s; i <= e; i += 1) mask[i] = 1;
  });
  return mask;
};

const buildPrimerMask = (ranges: PrimerRange1Based[], seqLen: number): Uint8Array => {
  const mask = new Uint8Array(seqLen); // bit: 1=left, 2=right
  ranges.forEach((r) => {
    const s = Number(r.start);
    const e = Number(r.end);
    if (!Number.isFinite(s) || !Number.isFinite(e)) return;
    const left1 = Math.max(1, Math.min(s, e));
    const right1 = Math.min(seqLen, Math.max(s, e));
    if (right1 < left1) return;
    const bit = r.kind === "left" ? 1 : 2;
    for (let i = left1 - 1; i <= right1 - 1; i += 1) mask[i] |= bit;
  });
  return mask;
};

const BLOCK_BASE_WIDTH_EM = 0.66;

type SeqBlock = {
  start0: number;
  end0: number;
  len: number;
  refPos: number; // 1-based
};

const buildBlocks = (seqLen: number, blockLen: number): SeqBlock[] => {
  const len = Math.max(1, Math.min(200, Math.floor(blockLen)));
  const out: SeqBlock[] = [];
  for (let start0 = 0; start0 < seqLen; start0 += len) {
    const end0 = Math.min(seqLen - 1, start0 + len - 1);
    out.push({
      start0,
      end0,
      len: end0 - start0 + 1,
      refPos: start0 + 1,
    });
  }
  return out;
};

const tickPositionsForBlock = (startPos: number, endPos: number): number[] => {
  const ticks: number[] = [];
  const first10 = Math.ceil(startPos / 10) * 10;
  for (let p = first10; p <= endPos; p += 10) ticks.push(p);
  return ticks;
};

export const FeatureSequenceView: React.FC<{
  sequence: string;
  header?: string;
  exonRanges?: Range1Based[];
  cdsRanges?: Range1Based[];
  primerRanges?: PrimerRange1Based[];
  highlightRange?: Range1Based | null;
  blockLen?: number;
  fontSize?: string;
}> = ({
  sequence,
  header,
  exonRanges = [],
  cdsRanges = [],
  primerRanges = [],
  highlightRange,
  blockLen = 60,
  fontSize = "0.95rem",
}) => {
  const normalized = useMemo(
    () => (sequence || "").replace(/\s+/g, "").toUpperCase(),
    [sequence],
  );
  const seqLen = normalized.length;

  const exonRanges0 = useMemo(() => clampRanges0(exonRanges, seqLen), [exonRanges, seqLen]);
  const cdsRanges0 = useMemo(() => clampRanges0(cdsRanges, seqLen), [cdsRanges, seqLen]);
  const highlight0 = useMemo(
    () => (highlightRange ? clampRanges0([highlightRange], seqLen)[0] ?? null : null),
    [highlightRange, seqLen],
  );

  const exonMask = useMemo(() => buildMask(exonRanges0, seqLen), [exonRanges0, seqLen]);
  const cdsMask = useMemo(() => buildMask(cdsRanges0, seqLen), [cdsRanges0, seqLen]);
  const primerMask = useMemo(() => buildPrimerMask(primerRanges, seqLen), [primerRanges, seqLen]);

  const blocks = useMemo(() => buildBlocks(seqLen, blockLen), [seqLen, blockLen]);

  if (!normalized) return null;

  const showExonLegend = exonRanges0.length > 0;
  const showCdsLegend = cdsRanges0.length > 0;
  const showPrimerLegend = primerRanges.length > 0;
  const showHighlightLegend = Boolean(highlight0);
  const showLegend =
    showExonLegend || showCdsLegend || showPrimerLegend || showHighlightLegend;

  return (
    <div
      className="ab1-align is-seqview"
      style={{ ["--ab1-seq-font-size" as any]: fontSize } as React.CSSProperties}
    >
      {header ? <div className="fasta-header">{header}</div> : null}
      {showLegend ? (
        <div className="seq-track-legend" style={{ marginTop: header ? "0" : undefined }}>
          {showExonLegend ? (
            <span className="legend-item">
              <span
                className="legend-box"
                style={{
                  backgroundColor: "transparent",
                  border: "2px solid #16a34a",
                  height: "10px",
                }}
              />
              エキソン（枠）
            </span>
          ) : null}
          {showCdsLegend ? (
            <span className="legend-item">
              <span
                className="legend-box"
                style={{
                  backgroundColor: "transparent",
                  border: "2px solid #2563eb",
                  height: "10px",
                }}
              />
              CDS（枠）
            </span>
          ) : null}
          {showPrimerLegend ? (
            <>
              <span className="legend-item">
                <span className="legend-box" style={{ backgroundColor: "#dbeafe" }} />
                Primer F
              </span>
              <span className="legend-item">
                <span className="legend-box" style={{ backgroundColor: "#ede9fe" }} />
                Primer R
              </span>
            </>
          ) : null}
          {showHighlightLegend ? (
            <span className="legend-item">
              <span className="legend-box" style={{ backgroundColor: "#fef3c7" }} />
              選択/ガイド
            </span>
          ) : null}
        </div>
      ) : null}

      {blocks.map((b) => {
        const startPos = b.refPos;
        const endPos = b.refPos + b.len - 1;
        const blockWidth = `${b.len * BLOCK_BASE_WIDTH_EM}em`;

        const addSeg = (
          segs: Array<{ key: string; className: string; leftPct: number; widthPct: number }>,
          key: string,
          className: string,
          s0: number,
          e0: number,
        ) => {
          const s = Math.max(b.start0, Math.min(s0, e0));
          const e = Math.min(b.end0, Math.max(s0, e0));
          if (e < s) return;
          segs.push({
            key,
            className,
            leftPct: (100 * (s - b.start0)) / Math.max(1, b.len),
            widthPct: (100 * (e - s + 1)) / Math.max(1, b.len),
          });
        };

        const featureSegs: Array<{ key: string; className: string; leftPct: number; widthPct: number }> = [];
        exonRanges0.forEach((r, i) => addSeg(featureSegs, `exon-${b.refPos}-${i}`, "feature exon", r.start0, r.end0));
        cdsRanges0.forEach((r, i) => addSeg(featureSegs, `cds-${b.refPos}-${i}`, "feature cds", r.start0, r.end0));
        if (highlight0) {
          addSeg(featureSegs, `hl-${b.refPos}`, "feature amplicon", highlight0.start0, highlight0.end0);
        }

        const tickPositions = tickPositionsForBlock(startPos, endPos);

        return (
          <div key={`feature-block-${b.refPos}`} id={`feature-seq-${b.refPos}`} className="ab1-seq-block">
            <div className="ab1-align-line ab1-seq-ruler-line">
              <span className="ab1-align-label" aria-hidden="true">
                {" "}
              </span>
              <div className="ab1-seq-ruler" style={{ width: blockWidth }}>
                {tickPositions.map((p) => (
                  <div
                    key={`tick-${b.refPos}-${p}`}
                    className="ab1-seq-ruler-tick"
                    style={{ left: `${(100 * (p - startPos)) / Math.max(1, b.len)}%` }}
                    title={`ref ${p.toLocaleString()}`}
                  >
                    {p % 50 === 0 ? <span className="ab1-seq-ruler-num">{p.toLocaleString()}</span> : null}
                  </div>
                ))}
              </div>
            </div>

            <div className="ab1-align-line">
              <span className="ab1-align-label">Ref {b.refPos}</span>
              <div className="ab1-seq-code-wrap" style={{ width: blockWidth }}>
                {featureSegs.length ? (
                  <div className="ab1-seq-feature-overlay" aria-hidden="true">
                    {featureSegs.map((s) => (
                      <div
                        key={s.key}
                        className={`ab1-seq-feature-box ${s.className}`}
                        style={{ left: `${s.leftPct}%`, width: `${s.widthPct}%` }}
                        aria-hidden="true"
                      />
                    ))}
                  </div>
                ) : null}
                <code className="ab1-align-code">
                  {Array.from({ length: b.len }, (_, idx) => {
                    const refIdx = b.start0 + idx;
                    const base = normalized[refIdx] ?? "N";
                    const isExon = exonMask[refIdx] === 1;
                    const isCds = cdsMask[refIdx] === 1;
                    const p = primerMask[refIdx] ?? 0;
                    const primerKind = p === 1 ? "left" : p === 2 ? "right" : p === 3 ? "both" : null;
                    return (
                      <span
                        // eslint-disable-next-line react/no-array-index-key
                        key={idx}
                        className={[
                          "ab1-base",
                          isExon ? "is-exon" : "",
                          isCds ? "is-cds" : "",
                          primerKind ? `is-primer ${primerKind}` : "",
                        ].join(" ")}
                      >
                        {base}
                      </span>
                    );
                  })}
                </code>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};
