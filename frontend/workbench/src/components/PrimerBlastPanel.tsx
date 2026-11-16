import React, { useEffect, useState } from "react";
import { bioapiBaseUrl, bioapiClient } from "../api/bioapiClient";
import { apiPostJson } from "../api/http";
import { useLocalBlastDbOptions, usePreferredLocalDbPaths } from "../utils/localBlastDbs";
import { engineDbKey, isEngineDbSelected, removeEngineDbSelection, resolveEngineDbs } from "../utils/primerEngineDbs";
import { pollJobUntilDone } from "../utils/jobPolling";
import { useLanguage } from "../utils/language";
import { useWorkbench } from "../utils/workbenchContext";
import type { JobCreateResponse, JobInfo } from "../types/jobs";
import { FeatureSequenceView } from "./FeatureSequenceView";
import { JobProgressCard } from "./JobProgressCard";

type Product = { subject: string; start: number; end: number; size: number };
type Pair = {
  index: number; forward: string; reverse: string; product_size: number;
  left_start: number; left_len: number; right_start: number; right_len: number;
  tm_f: number; tm_r: number;
  specificity: { specificity_status: string; search_complete_all_db: boolean;
    total_off_target: number; per_db: { db: string; search_completeness: string;
      on_target: Product[]; off_target: Product[] }[] };
};
type Result = { templates: { template_id: string; template_sequence: string; pairs: Pair[] }[] };

export const PrimerBlastPanel: React.FC = () => {
  const [language] = useLanguage();
  const en = language === "en";
  const text = (ja: string, english: string) => en ? english : ja;
  const { options, loading: dbLoading, error: dbError } = useLocalBlastDbOptions();
  const [dbs, setDbs] = usePreferredLocalDbPaths();
  const [customDb, setCustomDb] = useState("");
  const [sequence, setSequence] = useState("");
  const { presetSequenceInput, setPresetSequenceInput } = useWorkbench();
  useEffect(() => {
    if (presetSequenceInput?.target !== "primer_blast" || !presetSequenceInput.sequence) return;
    setSequence(presetSequenceInput.sequence);
    setPresetSequenceInput?.(null);
  }, [presetSequenceInput, setPresetSequenceInput]);
  const [productSize, setProductSize] = useState("200-1000");
  const [count, setCount] = useState(5);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobInfo | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [selected, setSelected] = useState(0);
  const pairs = result?.templates.flatMap(t => t.pairs.map(pair => ({ pair, template: t }))) ?? [];
  const chosen = pairs[selected];

  const run = async () => {
    setError("");
    const selectedDbs = resolveEngineDbs(dbs, options, customDb);
    if (!sequence.trim() || !selectedDbs.length) {
      setError("missing-inputs");
      return;
    }
    setBusy(true); setResult(null); setSelected(0); setJob(null); setJobId(null);
    try {
      const created = await apiPostJson<object, JobCreateResponse>(bioapiBaseUrl, "/primers/screen_job", {
        template: sequence, db: selectedDbs, product_size: productSize, num_return: count,
      });
      setJobId(created.job_id);
      const info = await pollJobUntilDone(created.job_id, { onUpdate: setJob });
      if (info.status !== "succeeded") throw new Error(info.error ?? info.status);
      setResult(await bioapiClient.getJobResult<Result>(created.job_id));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const cancel = async () => {
    if (!jobId) return;
    try { setJob(await bioapiClient.cancelJob(jobId)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  return <section className="panel" style={{ display: "grid", gap: 16 }}>
    <div><h2>{text("PCR設計・特異性確認", "PCR design and specificity")}</h2>
      <p>{text("配列と参照DBを選ぶと、組み込みのPrimerBLAST OSSで候補と予測PCR産物を調べます。", "Select your sequence and reference databases to design and screen primers with embedded PrimerBLAST OSS.")}</p></div>
    <label>{text("1. 対象配列（DNA / FASTA）", "1. Target sequence (DNA / FASTA)")}
      <textarea rows={5} value={sequence} onChange={e => setSequence(e.target.value)} style={{ width: "100%" }} disabled={busy} /></label>
    <fieldset disabled={busy}><legend>{text("2. 検索する参照DB（複数選択可）", "2. Reference databases (multiple selections allowed)")}</legend>
      {dbLoading && <p>{text("DB一覧を読み込み中…", "Loading databases…")}</p>}
      {dbError && <p role="alert">{dbError}</p>}
      {options.map(o => { const path = engineDbKey(o); return <label key={path} style={{ display: "inline-flex", gap: 6, marginRight: 16 }}>
        <input type="checkbox" checked={isEngineDbSelected(dbs, o)} onChange={e => setDbs(old => e.target.checked ? [...old, path] : removeEngineDbSelection(old, o))} />{o.label}</label>; })}
      {dbs.filter(path => !options.some(o => isEngineDbSelected([path], o))).map(path => <label key={path} style={{ display: "block" }}>
        <input type="checkbox" checked onChange={() => setDbs(old => old.filter(p => p !== path))} /> {path} ({text("保存済みの選択・現在の一覧にありません", "saved selection; not in the current list")})</label>)}
      <label style={{ display: "block", marginTop: 12 }}>{text("追加のローカルDB（任意・文字入力）", "Additional local DB (optional text input)")}
        <input value={customDb} onChange={e => setCustomDb(e.target.value)} style={{ width: "100%" }} /></label>
    </fieldset>
    <div style={{ display: "flex", gap: 24, alignItems: "end", flexWrap: "wrap" }}>
      <label>{text("産物長の範囲（bp）", "Product size range (bp)")}<input value={productSize} onChange={e => setProductSize(e.target.value)} disabled={busy} /></label>
      <label>{text("候補数", "Number of candidates")}<input type="number" min={1} max={50} value={count} onChange={e => setCount(Number(e.target.value))} disabled={busy} /></label>
      <button onClick={() => void run()} disabled={busy}>{busy ? text("解析中…", "Running…") : text("3. 設計して特異性を確認", "3. Design and screen")}</button>
    </div>
    <JobProgressCard jobId={jobId} job={job} onCancel={busy ? () => void cancel() : null} />
    {busy && <p>{text("停止要求は実行中の設計・検索が終わった時点で反映されます。", "Cancellation takes effect after the current design/search finishes.")}</p>}
    {error && <p role="alert" style={{ color: "#b91c1c" }}>{error === "missing-inputs" ? text("配列と参照DBを指定してください。", "Supply a sequence and a reference database.") : error}</p>}
    {result && <>
      <p>{text("in-silico予測です。Wet未検証。期待サイズ付近の産物はサイズで分類しており、狙った遺伝子の証明ではありません。検索未完了は特異性ありと判定できません。", "In-silico prediction; Wet unverified. Expected-size products are classified by size, not proven target identity. Incomplete searches cannot establish specificity.")}</p>
      {!pairs.length && <p>{text("候補がありません。配列長や産物長の条件を確認してください。", "No candidates. Check sequence length and product size limits.")}</p>}
      {!!pairs.length && <table><thead><tr><th>{text("表示", "View")}</th><th>bp</th><th>F / R (5′→3′)</th><th>Tm F / R</th><th>{text("検索", "Search")}</th><th>{text("非期待サイズ産物", "Other-size products")}</th></tr></thead>
        <tbody>{pairs.map(({ pair: p }, i) => <tr key={i}><td><button onClick={() => setSelected(i)} aria-pressed={selected === i}>{i + 1}</button></td><td>{p.product_size}</td>
          <td><code>{p.forward}</code><br /><code>{p.reverse}</code></td><td>{p.tm_f.toFixed(1)} / {p.tm_r.toFixed(1)}</td>
          <td>{p.specificity.search_complete_all_db ? text("完了", "Complete") : text("未完了・判定保留", "Incomplete / indeterminate")}</td><td>{p.specificity.total_off_target}</td></tr>)}</tbody></table>}
      {chosen && <><FeatureSequenceView sequence={chosen.template.template_sequence} header={chosen.template.template_id}
        primerRanges={[{ kind: "left", start: chosen.pair.left_start + 1, end: chosen.pair.left_start + chosen.pair.left_len },
          { kind: "right", start: chosen.pair.right_start - chosen.pair.right_len + 2, end: chosen.pair.right_start + 1 }]}
        highlightRange={{ start: chosen.pair.left_start + 1, end: chosen.pair.right_start + 1 }} />
        <h3>{text("予測PCR産物の位置と長さ", "Predicted PCR product positions and lengths")}</h3>
        {chosen.pair.specificity.per_db.map(d => <div key={d.db}><strong>{d.db}</strong> · {d.search_completeness}
          <ul>{[...d.on_target, ...d.off_target].map((a, i) => <li key={i}>{a.subject}: {a.start}–{a.end} · {a.size} bp</li>)}</ul>
          {!d.on_target.length && !d.off_target.length && <p>{text("今回の検索条件では産物が観測されませんでした。", "No products observed under these search conditions.")}</p>}</div>)}
      </>}
    </>}
  </section>;
};
