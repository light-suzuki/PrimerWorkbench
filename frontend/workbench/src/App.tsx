import React, { Suspense, useEffect, useState } from "react";
import { BackendHealthPill } from "./components/BackendHealthPill";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { useLocalStorageFlag } from "./utils/storage";
import { WorkbenchContext } from "./utils/workbenchContext";
import { useLanguage } from "./utils/language";
import { applyUiLanguage } from "./utils/uiTranslation";
import { focusedTabs, productName } from "./focusedTabs";

type TabId = (typeof focusedTabs)[number]["id"];

export const App: React.FC = () => {
  const [language, setLanguage] = useLanguage();
  const [activeTab, setActiveTab] = useState<TabId>(focusedTabs[0].id);
  const [mountedTabs, setMountedTabs] = useState<TabId[]>([focusedTabs[0].id]);
  const [heroCollapsed, setHeroCollapsed] = useLocalStorageFlag("seqwb_ui_hero_collapsed", false);
  const [presetReversePair, setPresetReversePair] = useState<{ primer1: string; primer2: string } | null>(null);
  const [presetBlastQuery, setPresetBlastQuery] = useState<{ sequence: string; label?: string } | null>(null);
  const [presetSequenceInput, setPresetSequenceInput] = useState<{ sequence: string; label?: string } | null>(null);
  const active = focusedTabs.find((tab) => tab.id === activeTab) ?? focusedTabs[0];

  useEffect(() => {
    setMountedTabs((previous) => previous.includes(activeTab) ? previous : [...previous, activeTab]);
  }, [activeTab]);

  useEffect(() => {
    document.title = productName;
  }, []);

  useEffect(() => applyUiLanguage(language), [language]);

  const text = (ja: string, en: string) => language === "ja" ? ja : en;

  return (
    <WorkbenchContext.Provider value={{
      setActiveTab: (id) => setActiveTab(id as TabId),
      presetReversePair, setPresetReversePair,
      presetBlastQuery, setPresetBlastQuery,
      presetSequenceInput, setPresetSequenceInput,
    }}>
      <div className="app-shell">
        <header className={`app-hero ${heroCollapsed ? "is-collapsed" : ""}`}>
          <div className="hero-left">
            <p className="hero-kicker">Local-first bioinformatics • Windows + WSL</p>
            <h1 className="hero-title">{productName}</h1>
            <p className="app-subtitle">{text("自分の配列とデータベースを使うローカル解析ツール。", "Local analysis tools for your own sequences and databases.")}</p>
          </div>
          <div className="hero-meta">
            <button type="button" className="hero-toggle" onClick={() => setLanguage(language === "en" ? "ja" : "en")}>
              {language === "en" ? "日本語" : "English"}
            </button>
            <BackendHealthPill />
            <button type="button" className="hero-toggle" onClick={() => setHeroCollapsed((value) => !value)}>
              {text(heroCollapsed ? "ヘッダー展開" : "ヘッダー折りたたむ", heroCollapsed ? "Expand header" : "Collapse header")}
            </button>
            <button type="button" className="hero-toggle" onClick={() => window.print()}>
              {text("印刷 / PDF保存", "Print / Save PDF")}
            </button>
          </div>
        </header>
        <nav className="tab-nav" aria-label="Workbench sections">
          {focusedTabs.map((tab) => (
            <button key={tab.id} type="button" className={`tab-button ${activeTab === tab.id ? "is-active" : ""}`}
              onClick={() => setActiveTab(tab.id)} title={text(tab.descriptionJa, tab.descriptionEn)}
              style={{ ["--tab-accent" as string]: tab.color } as React.CSSProperties}>
              <span className="tab-label">{text(tab.labelJa, tab.labelEn)}</span>
            </button>
          ))}
        </nav>
        <div className="tab-active-hint" style={{ ["--tab-accent" as string]: active.color } as React.CSSProperties}>
          <span className="tab-active-pill">{text(active.labelJa, active.labelEn)}</span>
          <span className="tab-active-desc">{text(active.descriptionJa, active.descriptionEn)}</span>
        </div>
        <main className="app-main">
          <ErrorBoundary title={productName}>
            <Suspense fallback={<div className="panel-card"><p>{text("読み込み中...", "Loading...")}</p></div>}>
              {focusedTabs.map((tab) => {
                const Panel = tab.Component;
                return mountedTabs.includes(tab.id) ? (
                  <div key={tab.id} className={`panel-card ${activeTab === tab.id ? "" : "panel-hidden"}`}>
                    <Panel />
                  </div>
                ) : null;
              })}
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
    </WorkbenchContext.Provider>
  );
};
