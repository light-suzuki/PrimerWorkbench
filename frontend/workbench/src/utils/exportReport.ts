export const downloadMarkdown = (markdown: string, baseName: string): void => {
  if (!markdown.trim()) return;
  const blob = new Blob([markdown], {
    type: "text/markdown;charset=utf-8",
  });
  const a = document.createElement("a");
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const safeBase = baseName || "report";
  a.download = `${safeBase}_${ts}.md`;
  a.href = URL.createObjectURL(blob);
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
};

export const downloadTextFile = (
  content: string,
  baseName: string,
  opts?: { ext?: string; mime?: string },
): void => {
  if (!content.trim()) return;
  const ext = (opts?.ext || "txt").replace(/^[.]+/, "") || "txt";
  const mime = opts?.mime || "text/plain;charset=utf-8";
  const blob = new Blob([content], { type: mime });
  const a = document.createElement("a");
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const safeBase = baseName || "export";
  a.download = `${safeBase}_${ts}.${ext}`;
  a.href = URL.createObjectURL(blob);
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
};

export const downloadFasta = (fasta: string, baseName: string): void => {
  downloadTextFile(fasta, baseName, { ext: "fasta", mime: "text/plain;charset=utf-8" });
};

export const downloadHtml = (html: string, baseName: string): void => {
  downloadTextFile(html, baseName, { ext: "html", mime: "text/html;charset=utf-8" });
};

export const openPrintViewForMarkdown = (markdown: string, title?: string): void => {
  if (!markdown.trim()) return;
  const win = window.open("", "_blank");
  if (!win) return;
  const escaped = markdown
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const docTitle = title || "Sequence Workbench Report";
  win.document.write(
    `<html><head><title>${docTitle}</title><style>
      body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 16px; }
      pre { white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: 12px; }
      </style></head><body><pre>${escaped}</pre></body></html>`,
  );
  win.document.close();
  win.focus();
  win.print();
};

const collectInlineStyles = (): string => {
  if (typeof document === "undefined") return "";
  const styles = Array.from(document.querySelectorAll("style"))
    .map((s) => s.textContent || "")
    .filter(Boolean)
    .join("\n");
  return styles;
};

const collectLinkedStylesheets = async (): Promise<string> => {
  if (typeof document === "undefined" || typeof window === "undefined") return "";
  const links = Array.from(document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]'));
  if (!links.length) return "";

  const cssTexts: string[] = [];
  await Promise.all(
    links.map(async (link) => {
      const href = link.href;
      if (!href) return;
      try {
        const res = await fetch(href, { credentials: "same-origin" });
        if (!res.ok) return;
        const text = await res.text();
        if (text.trim()) cssTexts.push(text);
      } catch {
        // ignore (offline/CORS)
      }
    }),
  );
  return cssTexts.join("\n");
};

const collectCssForExport = async (): Promise<string> => {
  const inline = collectInlineStyles();
  const linked = await collectLinkedStylesheets();
  return [inline, linked].filter(Boolean).join("\n");
};

export const buildStandaloneHtmlFromElement = (el: HTMLElement, opts?: { title?: string; bodyClass?: string; extraCss?: string; extraHead?: string; extraScript?: string; cssText?: string }): string => {
  const title = opts?.title || "Sequence Workbench Report";
  const bodyClass = (opts?.bodyClass || "").trim();
  const inlineCss = opts?.cssText ?? collectInlineStyles();
  const extraCss = opts?.extraCss || "";
  const extraHead = opts?.extraHead || "";
  const extraScript = opts?.extraScript || "";
  const bodyHtml = el.outerHTML;
  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>${title.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</title>
    <style>
      body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 16px; }
      ${inlineCss}
      ${extraCss}
    </style>
    ${extraHead}
  </head>
  <body class="${bodyClass}">
    ${bodyHtml}
    ${extraScript ? `<script>${extraScript}</script>` : ""}
  </body>
</html>`;
};

export const downloadElementAsHtml = async (
  el: HTMLElement,
  baseName: string,
  opts?: { title?: string; bodyClass?: string; extraCss?: string; extraHead?: string; extraScript?: string },
): Promise<void> => {
  if (!el) return;
  const cssText = await collectCssForExport();
  const html = buildStandaloneHtmlFromElement(el, { ...opts, cssText });
  downloadHtml(html, baseName);
};

export const openPrintViewForElement = (el: HTMLElement, opts?: { title?: string; bodyClass?: string; extraCss?: string; extraHead?: string; extraScript?: string }): void => {
  if (!el) return;
  const html = buildStandaloneHtmlFromElement(el, opts);
  const win = window.open("", "_blank");
  if (!win) return;
  win.document.open();
  win.document.write(html);
  win.document.close();
  win.focus();
  // Delay to let layout settle before print dialog.
  setTimeout(() => win.print(), 50);
};
