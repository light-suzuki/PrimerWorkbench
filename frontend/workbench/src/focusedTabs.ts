import React from "react";

const PrimerPanel = React.lazy(async () => ({ default: (await import("./components/PrimerPanel")).PrimerPanel }));
const PrimerBlastPanel = React.lazy(async () => ({ default: (await import("./components/PrimerBlastPanel")).PrimerBlastPanel }));
const PrimerReversePanel = React.lazy(async () => ({ default: (await import("./components/PrimerReversePanel")).PrimerReversePanel }));

export const productName = "Primer Workbench";
export const focusedTabs = [
  { id: "primers", labelJa: "プライマー", labelEn: "Primer design", descriptionJa: "Primer3でプライマーを設計", descriptionEn: "Design primers with Primer3", color: "#7c3aed", Component: PrimerPanel },
  { id: "primer_blast", labelJa: "PrimerBLAST", labelEn: "PrimerBLAST", descriptionJa: "候補をローカルDBで確認", descriptionEn: "Check candidates against local databases", color: "#4338ca", Component: PrimerBlastPanel },
  { id: "primer_reverse", labelJa: "Primer逆引き", labelEn: "Primer lookup", descriptionJa: "既存ペアの位置と増幅産物を探索", descriptionEn: "Locate existing pairs and predicted amplicons", color: "#4f46e5", Component: PrimerReversePanel },
] as const;

