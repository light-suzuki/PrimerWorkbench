import React, { createContext, useContext, useMemo, useRef } from "react";

type VisualGenomeView = {
  chrom: string;
  start: number;
  end: number;
  width: number;
};

export const VisualGenomeBrowserContext = createContext<VisualGenomeView | null>(null);

export const useVisualGenomeBrowser = (): VisualGenomeView => {
  const value = useContext(VisualGenomeBrowserContext);
  if (!value) {
    return { chrom: "", start: 1, end: 1, width: 800 };
  }
  return value;
};

type Props = {
  chrom: string;
  start: number;
  end: number;
  height?: number;
  children?: React.ReactNode;
  onViewChange?: (start: number, end: number) => void;
};

const clampRange = (start: number, end: number): { start: number; end: number } => {
  const left = Math.max(1, Math.floor(Math.min(start, end)));
  const right = Math.max(left + 1, Math.ceil(Math.max(start, end)));
  return { start: left, end: right };
};

export const VisualGenomeBrowser: React.FC<Props> = ({
  chrom,
  start,
  end,
  height = 200,
  children,
  onViewChange,
}) => {
  const width = 960;
  const dragStartX = useRef<number | null>(null);
  const dragStartRange = useRef<{ start: number; end: number } | null>(null);
  const range = clampRange(start, end);
  const span = Math.max(1, range.end - range.start);

  const view = useMemo(
    () => ({ chrom, start: range.start, end: range.end, width }),
    [chrom, range.start, range.end],
  );

  const updateRange = (nextStart: number, nextEnd: number) => {
    const next = clampRange(nextStart, nextEnd);
    onViewChange?.(next.start, next.end);
  };

  return (
    <div
      className="visual-genome-browser"
      style={{
        border: "1px solid var(--seq-border, #d4d4d8)",
        borderRadius: 6,
        overflow: "hidden",
        background: "#fff",
      }}
    >
      <svg
        role="img"
        aria-label={`${chrom} ${range.start}-${range.end}`}
        viewBox={`0 0 ${width} ${height}`}
        style={{ display: "block", width: "100%", height }}
        onPointerDown={(event) => {
          dragStartX.current = event.clientX;
          dragStartRange.current = range;
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (dragStartX.current == null || !dragStartRange.current) return;
          const dx = event.clientX - dragStartX.current;
          const basesPerPx = span / width;
          const shift = Math.round(-dx * basesPerPx);
          updateRange(dragStartRange.current.start + shift, dragStartRange.current.end + shift);
        }}
        onPointerUp={() => {
          dragStartX.current = null;
          dragStartRange.current = null;
        }}
        onWheel={(event) => {
          event.preventDefault();
          const rect = event.currentTarget.getBoundingClientRect();
          const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
          const anchor = range.start + Math.round(span * ratio);
          const factor = event.deltaY < 0 ? 0.8 : 1.25;
          const nextSpan = Math.max(20, Math.round(span * factor));
          updateRange(anchor - Math.round(nextSpan * ratio), anchor + Math.round(nextSpan * (1 - ratio)));
        }}
      >
        <rect x={0} y={0} width={width} height={height} fill="#fff" />
        <VisualGenomeBrowserContext.Provider value={view}>
          {children}
        </VisualGenomeBrowserContext.Provider>
      </svg>
    </div>
  );
};
