import React, { useEffect, useMemo, useState } from "react";
import { parsePrimerTable, parsePrimerText, pairsToFasta, type PrimerLayout } from "../utils/primerTable";
import { readPrimerFile, type PrimerSheet } from "../utils/primerWorkbook";
import { PrimerOrderSheet } from "./PrimerOrderSheet";

export const PrimerTableImport: React.FC<{ text: string; disabled?: boolean; onApply: (text: string, warning: string) => void }> = ({ text, disabled, onApply }) => {
  const [sheets, setSheets] = useState<PrimerSheet[]>([]);
  const [sheet, setSheet] = useState(0);
  const [layout, setLayout] = useState<PrimerLayout>("auto");
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [swapped, setSwapped] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const parsed = useMemo(() => sheets[sheet] ? parsePrimerTable(sheets[sheet].rows, layout, sheets[sheet].name) : parsePrimerText(text, layout), [text, sheets, sheet, layout]);
  useEffect(() => { setExcluded(new Set()); setSwapped(new Set()); }, [text, sheets, sheet, layout]);
  const selectedPairs = useMemo(() => parsed.pairs.flatMap((pair, i) => excluded.has(i) ? [] : [swapped.has(i) ? { forward: pair.reverse, reverse: pair.forward } : pair]), [parsed, excluded, swapped]);
  const read = async (file?: File) => {
    if (!file) return;
    setLoading(true); setError(""); setSheets([]);
    try { setSheets(await readPrimerFile(file)); setSheet(0); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  };
  const toggle = (setter: React.Dispatch<React.SetStateAction<Set<number>>>, index: number) => setter(old => {
    const next = new Set(old); if (next.has(index)) next.delete(index); else next.add(index); return next;
  });
  const apply = () => {
    const pairs = parsed.pairs.filter((_, i) => !excluded.has(i)).map(pair => {
      const index = parsed.pairs.indexOf(pair);
      return swapped.has(index) ? { forward: pair.reverse, reverse: pair.forward } : pair;
    });
    onApply(pairsToFasta(pairs), `${pairs.length}ペアを確認して取り込みました。${parsed.unpaired.length ? ` ペア未確定の${parsed.unpaired.length}本は検索に含めていません。` : ""}`);
    setSheets([]);
  };
  return <div className="ui-details-body" style={{ display: "grid", gap: 12 }}>
    <h3>Excel・表から読み取る</h3>
    <p>Excelのセルを上に貼り付けるか、ファイルを選択してください。ここでは検索せず、配列とペアを確認します。ファイルはブラウザ内で読み取ります。</p>
    <label>Excel / CSV / TSV ファイル <input type="file" accept=".xlsx,.csv,.tsv,.txt" disabled={disabled || loading} onChange={e => { void read(e.target.files?.[0]); e.target.value = ""; }} /></label>
    {loading && <p role="status">読み取り中…</p>}
    {error && <p role="alert" className="seq-error">{error}</p>}
    {!!sheets.length && <><label>読むシート <select value={sheet} disabled={disabled} onChange={e => setSheet(Number(e.target.value))}>{sheets.map((s, i) => <option key={i} value={i}>{s.name}</option>)}</select></label>
      <button disabled={disabled} onClick={() => setSheets([])}>貼り付けた内容を読む</button></>}
    <label>ペアの読み方 <select value={layout} disabled={disabled} onChange={e => setLayout(e.target.value as PrimerLayout)}>
      <option value="auto">自動（見出しを優先・名前なしは各列で上下）</option>
      <option value="columns">各列で上下2本が1ペア（縦の表が横に複数あっても可）</option>
      <option value="rows">横の2本で1ペア（1行に名前・Fw・Re）</option>
    </select></label>
    <details><summary>対応する表と記法の例</summary>
      <pre>{"横ペア（横の読み方を選択）\n名前\tFw\tRe\nMarker1\tACGTACGTACGT\tTGCATGCATGCA\n\n縦ペア（名前・方向で対応づけ）\n名前\t配列\nMarker1_Fw\tACGTACGTACGT\nMarker1_Re\tTGCATGCATGCA\n\n名前なし・縦の表が2列\nACGTACGTACGT\tGCTAGCTAGCTA\nTGCATGCATGCA\tTAGCTAGCTAGC"}</pre>
      <p>全角、空白入り配列、5′/3′表記、FASTA、余分な列を扱います。名前なしは上下順で仮に組み合わせます。Fw/Re情報がない場合は方向も仮定なので一覧を確認してください。旧.xlsは.xlsxで保存するかセルをコピーしてください。</p>
    </details>
    <p>{parsed.pairs.length}ペア / {parsed.unpaired.length}本がペア未確定</p>
    {parsed.warnings.map((warning, i) => <p className="seq-hint" key={i}>{warning}</p>)}
    {!!parsed.pairs.length && <div style={{ maxHeight: 360, overflow: "auto" }}><table><thead><tr><th>取り込む</th><th>名前・元の位置</th><th>Fw / Re 配列（5′→3′）</th><th>修正</th></tr></thead><tbody>
      {parsed.pairs.slice(0, 500).map((original, i) => { const pair = swapped.has(i) ? { forward: original.reverse, reverse: original.forward } : original; return <tr key={i}>
        <td><input type="checkbox" aria-label={`ペア${i + 1}を取り込む`} checked={!excluded.has(i)} disabled={disabled} onChange={() => toggle(setExcluded, i)} /></td>
        <td>{pair.forward.name} / {pair.reverse.name}<br /><small>{pair.forward.source} / {pair.reverse.source}</small></td>
        <td><code>{pair.forward.sequence}</code><br /><code>{pair.reverse.sequence}</code></td>
        <td><button disabled={disabled} onClick={() => toggle(setSwapped, i)}>Fw/Reを入れ替え</button></td></tr>; })}
    </tbody></table></div>}
    {parsed.pairs.length > 500 && <p role="alert">500ペアを超えます。範囲を分けて取り込んでください。</p>}
    {!!parsed.unpaired.length && <details><summary>ペア未確定の配列（検索には含めません）</summary>{parsed.unpaired.map((p, i) => <p key={i}>{p.name || "名前なし"} · {p.source} · <code>{p.sequence}</code></p>)}</details>}
    <button className="seq-button" disabled={disabled || loading || !!error || !parsed.pairs.length || parsed.pairs.length > 500 || excluded.size === parsed.pairs.length} onClick={apply}>確認したペアを検索入力へ取り込む</button>
    <PrimerOrderSheet pairs={selectedPairs} disabled={disabled || loading || !!error} />
  </div>;
};
