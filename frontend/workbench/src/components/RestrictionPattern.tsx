import React from "react";
import { useLanguage } from "../utils/language";

export interface CutWindow {
  site_start: number; strand: string; top_boundary: number; bottom_boundary: number;
  window_start: number; window_end: number; pattern: {top: string; bottom: string};
}
export interface EnzymeDetail {
  name: string; recognition: string; cuts: number[][];
  pattern?: {top: string; bottom: string} | null;
  product_names?: string[]; same_cut_enzymes: string[]; different_cut_enzymes: string[];
  prediction_supported: boolean; source: string; reference_url: string; windows?: CutWindow[];
}
export const RestrictionPattern: React.FC<{detail?: EnzymeDetail | null; windows?: CutWindow[]}> = ({detail, windows}) => {
  const [language] = useLanguage(); const en = language === "en";
  if (!detail) return null;
  return <div className="restriction-pattern">
    {windows === undefined && <>
    <strong>{detail.name} · {detail.recognition}</strong>
    {detail.pattern ? <pre>{detail.pattern.top}{"\n"}{detail.pattern.bottom}</pre> : <p>{en ? "Cleavage positions unknown" : "切断位置が不明"}</p>}
    {detail.cuts.map(([top,bottom], i) => <p key={i}>{top === bottom ? (en ? "Blunt ends" : "平滑末端") : `${top < bottom ? "5′" : "3′"} ${en ? "overhang" : "突出末端"} (${Math.abs(top-bottom)} nt)`}</p>)}
    {detail.product_names?.length ? <p>{en ? "Product names" : "確認済み製品名"}: {detail.product_names.join(", ")}</p> : null}
    {detail.same_cut_enzymes.length ? <p>{en ? "Same recognition / cleavage" : "同じ認識配列・同じ切断"}: {detail.same_cut_enzymes.join(", ")}</p> : null}
    {detail.different_cut_enzymes.length ? <p>{en ? "Same recognition, different cleavage" : "同じ認識配列・異なる切断"}: {detail.different_cut_enzymes.join(", ")}</p> : null}
    <p className="seq-hint">{en ? "| marks cleavage. Check product-specific methylation and reaction requirements; identical cleavage does not imply identical conditions." : "| が切断位置です。同じ切断でもメチル化感受性・反応条件は同一とは限りません。使用する製品の資料で確認してください。"}</p>
    {!detail.prediction_supported && <p>{en ? "Excluded from ordinary PCR automatic candidates." : "通常PCRの自動候補から除外する切断形式・基質です。"}</p>}
    </>}
    {windows !== undefined && !windows.length && <p>{en ? "No complete cleavage sites recorded in this allele." : "このアレルに記録された完全な切断部位はありません。"}</p>}
    {(windows ?? detail.windows ?? []).map((window,i) => <div key={i}>
      <p>{window.window_start}–{window.window_end} bp · {window.strand} · {en ? "Top / bottom cut boundaries" : "上鎖 / 下鎖の切断境界"}: {window.top_boundary} / {window.bottom_boundary}</p>
      <pre>{window.pattern.top}{"\n"}{window.pattern.bottom}</pre>
    </div>)}
    {windows === undefined && <small>{detail.source} · <a href={detail.reference_url} target="_blank" rel="noreferrer">REBASE</a></small>}
  </div>;
};
