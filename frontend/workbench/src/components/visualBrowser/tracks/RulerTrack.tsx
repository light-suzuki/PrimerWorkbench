import React from "react";
import { useVisualGenomeBrowser } from "../VisualGenomeBrowser";

type Props = {
  height?: number;
  y?: number;
};

const formatPosition = (value: number): string => {
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(2)} Mb`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)} kb`;
  return String(value);
};

export const RulerTrack: React.FC<Props> = ({ height = 30, y = 0 }) => {
  const view = useVisualGenomeBrowser();
  const span = Math.max(1, view.end - view.start);
  const tickCount: number = 6;
  const ticks = Array.from({ length: tickCount }, (_, index) => {
    const ratio = tickCount === 1 ? 0 : index / (tickCount - 1);
    return {
      x: ratio * view.width,
      value: Math.round(view.start + span * ratio),
    };
  });

  return (
    <g transform={`translate(0 ${y})`}>
      <rect x={0} y={0} width={view.width} height={height} fill="#f8fafc" />
      <line x1={0} y1={height - 8} x2={view.width} y2={height - 8} stroke="#64748b" strokeWidth={1} />
      <text x={8} y={14} fill="#334155" fontSize={11}>
        {view.chrom || "chromosome"}
      </text>
      {ticks.map((tick) => (
        <g key={`${tick.x}-${tick.value}`}>
          <line x1={tick.x} y1={height - 14} x2={tick.x} y2={height - 3} stroke="#64748b" strokeWidth={1} />
          <text x={tick.x + 4} y={height - 16} fill="#475569" fontSize={10}>
            {formatPosition(tick.value)}
          </text>
        </g>
      ))}
    </g>
  );
};
