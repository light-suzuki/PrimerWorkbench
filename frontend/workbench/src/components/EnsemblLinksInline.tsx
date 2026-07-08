import React, { useMemo } from "react";
import {
  ensemblGeneUrl,
  ensemblLocationUrl,
  ensemblTranscriptExportUrl,
  ensemblTranscriptSummaryUrl,
  inferEnsemblPlantsSpecies,
  inferEnsemblTranscriptId,
  isLocalOnlyDb,
  localReferenceGeneUrl,
  localReferenceLocationUrl,
} from "../utils/ensembl";

export const EnsemblLinksInline: React.FC<{
  geneId?: string | null;
  geneName?: string | null;
  dbLabel?: string | null;
  chrom?: string | null;
  start?: number | null;
  end?: number | null;
  showGene?: boolean;
  showLocation?: boolean;
  showTranscript?: boolean;
  showExport?: boolean;
}> = ({
  geneId,
  geneName,
  dbLabel,
  chrom,
  start,
  end,
  showGene = true,
  showLocation = true,
  showTranscript = true,
  showExport = true,
}) => {
    const resolvedGene = (geneId || geneName || "").trim();

    // ローカル専用 DB かどうかを判定
    const localOnly = useMemo(() => isLocalOnlyDb(dbLabel), [dbLabel]);

    const species = useMemo(
      () => inferEnsemblPlantsSpecies({ geneId: resolvedGene || null, dbLabel: dbLabel || null }),
      [dbLabel, resolvedGene],
    );

    const transcriptId = useMemo(
      () => inferEnsemblTranscriptId({ geneId: geneId || null, geneName: geneName || null }),
      [geneId, geneName],
    );

    // Gene URL: local DBs use the configured local reference browser.
    const geneUrl = useMemo(() => {
      if (!resolvedGene) return null;
      if (localOnly) {
        return localReferenceGeneUrl({ geneId: resolvedGene, dbLabel });
      }
      if (species) {
        return `https://plants.ensembl.org/${species}/Gene/Summary?g=${encodeURIComponent(resolvedGene)}`;
      }
      return ensemblGeneUrl(resolvedGene);
    }, [resolvedGene, species, localOnly, dbLabel]);

    // Location URL: local DBs use the configured local reference browser.
    const locUrl = useMemo(() => {
      if (localOnly) {
        return localReferenceLocationUrl({
          dbLabel,
          chrom: chrom || null,
          start: start ?? null,
          end: end ?? null,
        });
      }
      return ensemblLocationUrl({
        species,
        chrom: chrom || null,
        start: start ?? null,
        end: end ?? null,
      });
    }, [chrom, end, species, start, localOnly, dbLabel]);

    // Transcript URLs (Ensembl only - Navigator doesn't have transcript view)
    const transcriptSummaryUrl = useMemo(
      () =>
        !localOnly && transcriptId && resolvedGene
          ? ensemblTranscriptSummaryUrl({
            species,
            geneId: resolvedGene,
            transcriptId,
            chrom: chrom || null,
            start: start ?? null,
            end: end ?? null,
          })
          : null,
      [chrom, end, resolvedGene, species, start, transcriptId, localOnly],
    );

    const transcriptExportUrl = useMemo(
      () =>
        !localOnly && transcriptId && resolvedGene
          ? ensemblTranscriptExportUrl({
            species,
            geneId: resolvedGene,
            transcriptId,
            chrom: chrom || null,
            start: start ?? null,
            end: end ?? null,
          })
          : null,
      [chrom, end, resolvedGene, species, start, transcriptId, localOnly],
    );

    const hasAny =
      (showGene && geneUrl) ||
      (showLocation && locUrl) ||
      (showTranscript && transcriptSummaryUrl) ||
      (showExport && transcriptExportUrl);
    if (!hasAny) return null;

    return (
      <span className="seq-hint" style={{ display: "inline-flex", gap: "0.5rem", flexWrap: "wrap" }}>
        {showGene && geneUrl ? (
          <a href={geneUrl} target="_blank" rel="noreferrer">
            {localOnly ? "Local browser" : "Gene"}
          </a>
        ) : null}
        {showLocation && locUrl ? (
          <a href={locUrl} target="_blank" rel="noreferrer">
            Location
          </a>
        ) : null}
        {showTranscript && transcriptSummaryUrl ? (
          <a href={transcriptSummaryUrl} target="_blank" rel="noreferrer">
            Transcript
          </a>
        ) : null}
        {showExport && transcriptExportUrl ? (
          <a href={transcriptExportUrl} target="_blank" rel="noreferrer">
            Export
          </a>
        ) : null}
      </span>
    );
  };
