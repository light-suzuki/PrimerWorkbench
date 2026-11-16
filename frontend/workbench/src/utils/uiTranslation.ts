import { generatedEnglishUi } from "./translations.generated";
import type { Language } from "./language";

const japanese = /[ぁ-んァ-ヶ一-龠]/;
const decodeEntities = (value: string): string =>
  value.replaceAll("&apos;", "'").replaceAll("&quot;", '"').replaceAll("&amp;", "&");
const translatedUi = Object.fromEntries(
  Object.entries({ ...generatedEnglishUi,
    "注文票を作る": "Create an order sheet",
    "メーカー": "Manufacturer",
    "公式の注文方法・最新書式": "Official ordering instructions and current forms",
    "公式テンプレート": "Official template",
    "Invitrogen Value Oligos用 Excel を保存": "Download Invitrogen Value Oligos Excel",
    "ニッポンジーン公式書式へ転記して保存": "Fill and download the NIPPON GENE form",
    "公式の空の注文書（.xlsx）": "Blank official order form (.xlsx)",
    "メーカー用ファイルを生成しました。Excelで開き、条件・数式の再計算・顧客情報を確認してください。": "Manufacturer file generated. Open in Excel, recalculate formulas, and review synthesis options and customer details.",
    "このメーカーは共通の名前・配列一覧を転記する方式です。公式書式への自動記入は未対応です。Web注文画面でテンプレートを取得できる場合があります。": "Transfer the generic Name/Sequence list for this vendor. Automatic form filling is not supported. Templates may be available in the vendor ordering portal.",
    "公式テンプレートの列順・シート名に合わせたValue Oligos用XLSXです。1〜200本、5〜40塩基、ACGTのみ。地域によって提供されない場合があります。条件は注文画面で設定してください。": "Value Oligos XLSX follows the official column order and worksheet name. 1–200 oligos, 5–40 nt, ACGT only. Availability varies by region. Set synthesis options in the vendor portal.",
    "精製": "Purification",
    "乾燥": "Dry",
    "溶液（備考に条件を記載）": "Solution (specify conditions in notes)",
    "配列 5′→3′": "Sequence 5′→3′",
    "注文名": "Order name",
    "Eurofins 公式標準オリゴ注文書（日本語）": "Eurofins official standard oligo order form (Japanese)",
    "Eurofins 最新の公式注文書一覧": "Eurofins current official order forms",
    "FASMAC 公式注文ガイド": "FASMAC official ordering guide",
    "IDT 公式注文ガイド": "IDT official ordering guide",
    "汎用注文票 Excel を保存": "Download generic Excel order sheet",
    "メーカー貼付用 Excel（名前・配列）を保存": "Download Name/Sequence Excel for vendor entry",
    "合成スケール（メーカーの単位で入力）": "Synthesis scale (use vendor units)",
    "注文備考": "Order notes",
    "注文名（編集可）": "Order name (editable)",
    "納品形態": "Delivery format",
    "未指定": "Unspecified",
    "上で選択したペアのプライマーを出力します。検索やWet検証の合格を意味しません。注文の送信は行いません。": "Export primers from the selected pairs above. This does not certify specificity or wet validation. No order is submitted.",
    "本。未指定の条件は空欄で出力します。標準の未修飾DNA用です。修飾オリゴはメーカーの専用フォームで指定してください。": "oligos. Unspecified options remain blank. For unmodified DNA; specify modifications using the vendor form.",
    "メーカー貼付用は共通の2列一覧です。公式テンプレートそのものではありません。スケール・精製・配送情報はメーカー側で設定してください。": "The vendor-entry export is a generic two-column list, not an official upload template. Set scale, purification and shipping details at the vendor.",
    "注文票を出力しました。条件と配列を確認してメーカー側で注文してください。": "Order sheet exported. Review sequences and options before ordering at the vendor.",
    "同名のプライマーがあります。名前を区別してください。": "Duplicate primer names. Use distinct names.",
    "同じ配列が複数あります。自動ではまとめません。必要な本数を確認してください。": "Duplicate sequences are retained. Review the number of oligos needed.",
    "縮重塩基を含みます。メーカーが受け付ける記法を確認してください。": "Contains degenerate bases. Check the vendor notation.",
    "Excel・表から読み取る": "Import Excel or a table",
    "Excelのセルを上に貼り付けるか、ファイルを選択してください。ここでは検索せず、配列とペアを確認します。ファイルはブラウザ内で読み取ります。": "Paste Excel cells above or choose a file. Review sequences and pairs before searching. Files are read locally in your browser.",
    "Excel / CSV / TSV ファイル": "Excel / CSV / TSV file",
    "読むシート": "Worksheet",
    "貼り付けた内容を読む": "Read pasted text",
    "ペアの読み方": "Pair layout",
    "自動（見出しを優先・名前なしは各列で上下）": "Automatic: use headers; otherwise pair vertically in each column",
    "各列で上下2本が1ペア（縦の表が横に複数あっても可）": "Vertical: consecutive primers in each column, including parallel tables",
    "横の2本で1ペア（1行に名前・Fw・Re）": "Horizontal: two primers in each row (name / Fw / Re)",
    "対応する表と記法の例": "Supported layouts and examples",
    "名前・元の位置": "Names and source cells",
    "Fw / Re 配列（5′→3′）": "Fw / Re sequences (5′→3′)",
    "Fw/Reを入れ替え": "Swap Fw/Re",
    "確認したペアを検索入力へ取り込む": "Use reviewed pairs as search input",
    "ペア未確定の配列（検索には含めません）": "Unpaired primers (excluded from search)",
    "本がペア未確定": "unpaired primers",
    "読み取り中…": "Reading…",
    "500ペアを超えます。範囲を分けて取り込んでください。": "Over 500 pairs. Import a smaller range at a time.",
  }).map(([source, target]) => [decodeEntities(source), target]),
);
const entries = Object.entries(translatedUi).sort(([left], [right]) => right.length - left.length);
const originalText = new Map<Text, string>();
const originalAttributes = new Map<Element, Map<string, string>>();
let observer: MutationObserver | null = null;
let activeLanguage: Language = "ja";

const translate = (value: string): string => {
  if (!japanese.test(value)) return value;
  const compact = value.replace(/\s+/g, " ").trim();
  if (compact === "日本語") return value;
  const exact = translatedUi[compact];
  if (exact) return value.replace(compact, exact);
  let result = value;
  for (const [source, target] of entries) {
    if (source.length >= 2 && result.includes(source)) result = result.replaceAll(source, target);
  }
  return result;
};

const translateNode = (node: Node): void => {
  if (node.nodeType === Node.TEXT_NODE) {
    const text = node as Text;
    const parent = text.parentElement;
    if (!parent || parent.closest("script, style, code, pre, textarea")) return;
    const value = text.nodeValue ?? "";
    const translated = translate(value);
    if (translated !== value) {
      if (!originalText.has(text)) originalText.set(text, value);
      text.nodeValue = translated;
    }
    return;
  }
  if (!(node instanceof Element)) return;
  for (const attribute of ["title", "placeholder", "aria-label"]) {
    const value = node.getAttribute(attribute);
    if (!value) continue;
    const translated = translate(value);
    if (translated !== value) {
      if (!originalAttributes.has(node)) originalAttributes.set(node, new Map());
      const saved = originalAttributes.get(node)!;
      if (!saved.has(attribute)) saved.set(attribute, value);
      node.setAttribute(attribute, translated);
    }
  }
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  let child = walker.nextNode();
  while (child) {
    translateNode(child);
    child = walker.nextNode();
  }
  node.querySelectorAll("[title], [placeholder], [aria-label]").forEach((element) => translateNode(element));
};

const restore = (): void => {
  for (const [node, value] of originalText) {
    if (node.isConnected) node.nodeValue = value;
  }
  for (const [element, attributes] of originalAttributes) {
    if (!element.isConnected) continue;
    for (const [name, value] of attributes) element.setAttribute(name, value);
  }
  originalText.clear();
  originalAttributes.clear();
};

export const applyUiLanguage = (language: Language): void => {
  activeLanguage = language;
  observer?.disconnect();
  observer = null;
  if (language === "ja") {
    restore();
    return;
  }
  if (document.body) translateNode(document.body);
  observer = new MutationObserver((mutations) => {
    if (activeLanguage !== "en") return;
    for (const mutation of mutations) {
      mutation.addedNodes.forEach(translateNode);
      if (mutation.type === "characterData") translateNode(mutation.target);
      if (mutation.type === "attributes") translateNode(mutation.target);
    }
  });
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["title", "placeholder", "aria-label"],
  });
};
