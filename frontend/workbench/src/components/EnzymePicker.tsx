import React, { useState } from "react";
import { bioapiBaseUrl } from "../api/bioapiClient";
import { useLanguage } from "../utils/language";
import { RestrictionPattern, type EnzymeDetail } from "./RestrictionPattern";

export const EnzymePicker: React.FC<{onAdd: (name: string) => void}> = ({onAdd}) => {
  const [language] = useLanguage(); const en = language === "en";
  const [data,setData] = useState<EnzymeDetail[]>([]);
  const [query,setQuery] = useState(""); const [error,setError] = useState(false);
  const normalize = (value: string) => value.normalize("NFKC").replace(/\s+/g,"").toLowerCase();
  const matches = query.trim() ? data.filter(row => [row.name,row.recognition,...(row.product_names??[])].some(name => normalize(name).includes(normalize(query)))) : [];
  return <details onToggle={event => {
    if (event.currentTarget.open && !data.length) {
      setError(false);
      fetch(`${bioapiBaseUrl}/sequence/enzymes`).then(response => { if (!response.ok) throw new Error(); return response.json(); }).then(catalog => setData(catalog.enzymes.map((row: EnzymeDetail) => ({...row,source:`${catalog.release} / REBASE ${catalog.rebase_version}`})))).catch(() => setError(true));
    }
  }}><summary>{en ? "Find an enzyme / product name" : "酵素名・製品名から探す"}</summary>
    <label>{en ? "Enzyme or recognition sequence" : "酵素名・認識配列"}<input className="seq-input" value={query} onChange={event => setQuery(event.target.value)} placeholder="EcoRI-HF / MvaI / CCWGG" /></label>
    <p className="seq-hint">{en ? "1,088-name offline catalog (REBASE 2024). Product names and supplier conditions are not fully covered." : "1,088酵素のローカル一覧（REBASE 2024年版）。全製品名・反応条件の完全網羅ではありません。"}</p>
    {error && <p role="alert">{en ? "Could not load the local catalog." : "ローカルの酵素一覧を読み込めませんでした。"}</p>}
    {query.trim() && <p role="status">{matches.length} {en ? "matches; first 20 displayed" : "件（先頭20件を表示）"}</p>}
    {matches.slice(0,20).map(row => <details key={row.name}><summary>{row.name} · {row.recognition}</summary><RestrictionPattern detail={row} /><button type="button" className="seq-button secondary" onClick={() => onAdd(row.name)}>{en ? "Add to enzyme input" : "酵素入力に追加"}</button></details>)}
  </details>;
};
