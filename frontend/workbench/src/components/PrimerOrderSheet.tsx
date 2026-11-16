import React, { useEffect, useMemo, useState } from "react";
import type { ImportedPair } from "../utils/primerTable";
import { orderOligos, orderSheet, orderWarnings } from "../utils/primerOrder";
import { downloadXlsx } from "../utils/exportXlsx";
import { fillNipponWorkbook, orderVendors, thermoValueErrors, thermoValueSheet } from "../utils/vendorOrders";

export const PrimerOrderSheet: React.FC<{ pairs: ImportedPair[]; disabled?: boolean }> = ({ pairs, disabled }) => {
  const initial = useMemo(() => orderOligos(pairs), [pairs]);
  const [oligos, setOligos] = useState(initial);
  const [scale, setScale] = useState("");
  const [purification, setPurification] = useState("");
  const [delivery, setDelivery] = useState("");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("");
  const [vendorId, setVendorId] = useState("eurofins");
  const [template, setTemplate] = useState<File>();
  const [busy, setBusy] = useState(false);
  const vendor = orderVendors.find(v => v.id === vendorId)!;
  useEffect(() => { setOligos(initial); setStatus(""); }, [initial]);
  const warnings = orderWarnings(oligos);
  const blocked = disabled || !oligos.length || oligos.length > 1000 || oligos.some(p => !p.name.trim() || /[\t\r\n]/.test(p.name)) || new Set(oligos.map(p => p.name.trim())).size !== oligos.length;
  const exportFile = async (pasteOnly: boolean) => {
    setStatus("");
    try {
      await downloadXlsx([{ name: pasteOnly ? "Name_Sequence" : "Oligo_Order", data: pasteOnly ? [["Name", "Sequence"], ...oligos.map(p => [p.name, p.sequence])] : orderSheet(oligos, scale, purification, delivery, note) }], pasteOnly ? "primer_name_sequence" : "primer_order");
      setStatus("注文票を出力しました。条件と配列を確認してメーカー側で注文してください。");
    } catch (e) { setStatus(e instanceof Error ? e.message : String(e)); }
  };
  const exportVendor = async () => {
    setBusy(true); setStatus("");
    try {
      if (vendorId === "thermo") await downloadXlsx([thermoValueSheet(oligos)], "invitrogen_value_oligos");
      else if (vendorId === "nippon" && template) {
        const blob = await fillNipponWorkbook(template, oligos);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a"); a.href = url; a.download = "nippon_gene_order_draft.xlsx";
        document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      }
      setStatus("メーカー用ファイルを生成しました。Excelで開き、条件・数式の再計算・顧客情報を確認してください。");
    } catch (e) { setStatus(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <details className="ui-details"><summary>注文票を作る</summary><div className="ui-details-body" style={{ display: "grid", gap: 10 }}>
    <p>上で選択したペアのプライマーを出力します。検索やWet検証の合格を意味しません。注文の送信は行いません。</p>
    <p>{oligos.length}本。未指定の条件は空欄で出力します。標準の未修飾DNA用です。修飾オリゴはメーカーの専用フォームで指定してください。</p>
    <label>合成スケール（メーカーの単位で入力） <input value={scale} disabled={disabled} placeholder="例: 25 nmol" onChange={e => setScale(e.target.value)} /></label>
    <label>精製 <select value={purification} disabled={disabled} onChange={e => setPurification(e.target.value)}><option value="">未指定</option><option>Desalt</option><option>HPLC</option><option>PAGE</option><option>Cartridge</option></select></label>
    <label>納品形態 <select value={delivery} disabled={disabled} onChange={e => setDelivery(e.target.value)}><option value="">未指定</option><option value="Dry">乾燥</option><option value="Solution (specify in notes)">溶液（備考に条件を記載）</option></select></label>
    <label>注文備考 <input value={note} disabled={disabled} onChange={e => setNote(e.target.value)} /></label>
    {warnings.map(w => <p key={w} role="status">{w}</p>)}
    <div style={{ maxHeight: 250, overflow: "auto" }}><table><thead><tr><th>注文名（編集可）</th><th>配列 5′→3′</th><th>nt</th></tr></thead><tbody>{oligos.map((p, i) => <tr key={i}><td><input aria-label={`注文名 ${i + 1}`} value={p.name} disabled={disabled} onChange={e => setOligos(old => old.map((v, j) => i === j ? { ...v, name: e.target.value } : v))} /></td><td><code>{p.sequence}</code></td><td>{p.sequence.length}</td></tr>)}</tbody></table></div>
    <button disabled={blocked} onClick={() => void exportFile(false)}>汎用注文票 Excel を保存</button>
    <button disabled={blocked} onClick={() => void exportFile(true)}>メーカー貼付用 Excel（名前・配列）を保存</button>
    {status && <p role="status">{status}</p>}
    <p>メーカー貼付用は共通の2列一覧です。公式テンプレートそのものではありません。スケール・精製・配送情報はメーカー側で設定してください。</p>
    <label>メーカー <select value={vendorId} disabled={disabled || busy} onChange={e => { setVendorId(e.target.value); setTemplate(undefined); setStatus(""); }}>{orderVendors.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>
    <a href={vendor.guide} target="_blank" rel="noreferrer">公式の注文方法・最新書式</a>
    <ul>{vendor.templates.map(([name, url]) => <li key={url}><a href={url} target="_blank" rel="noreferrer">{name} — 公式テンプレート</a></li>)}</ul>
    {vendorId === "thermo" ? <>
      <p>公式テンプレートの列順・シート名に合わせたValue Oligos用XLSXです。1〜200本、5〜40塩基、ACGTのみ。地域によって提供されない場合があります。条件は注文画面で設定してください。</p>
      {thermoValueErrors(oligos).map(e => <p key={e} role="status">{e}</p>)}
      <button disabled={blocked || busy || !!thermoValueErrors(oligos).length} onClick={() => void exportVendor()}>Invitrogen Value Oligos用 Excel を保存</button>
    </> : vendorId === "nippon" ? <>
      <p>公式の空の注文書を上のリンクから保存して選択してください。ブラウザ内で試料名・配列だけ転記し、書式・数式を保持します。上の汎用条件は転記しません。公式書式の初期条件が残るため、出力後にExcelで開き、精製・納品形態・スケール・顧客情報を確認し、再計算して保存してください。1〜96本、試料名は半角英数字15文字以内です。</p>
      <label>公式の空の注文書（.xlsx） <input type="file" accept=".xlsx" disabled={disabled || busy} onChange={e => { setTemplate(e.target.files?.[0]); setStatus(""); e.target.value = ""; }} /></label>
      {template && <p>{template.name}</p>}
      <button disabled={blocked || busy || !template} onClick={() => void exportVendor()}>ニッポンジーン公式書式へ転記して保存</button>
    </> : <p>このメーカーは共通の名前・配列一覧を転記する方式です。公式書式への自動記入は未対応です。Web注文画面でテンプレートを取得できる場合があります。</p>}
  </div></details>;
};
