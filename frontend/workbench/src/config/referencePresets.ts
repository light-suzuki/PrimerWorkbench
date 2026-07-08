import type { NCBITarget } from "../types/blast";

export type ReferencePreset = {
  id: string;
  label: string;
  blastDbName: string;
  downloadUrl?: string;
  ncbiTarget?: NCBITarget;
};

// Public reference presets.
// Add a new plant/reference here when adapting this workbench to another genome.
// Required setup for each new reference:
// 1. Download or place the FASTA/GFF files outside this repository.
// 2. Build BLAST+ indexes with makeblastdb.
// 3. Register the makeblastdb prefix in DB Manager or set it as a preferred DB.
// 4. Add a preset below only if you want the UI to show a convenient example.
//
// Keep private lab genomes, API keys, and machine-local paths out of this file.
export const REFERENCE_PRESETS: ReferencePreset[] = [
  {
    id: "arabidopsis_thaliana",
    label: "Arabidopsis thaliana (TAIR10)",
    blastDbName: "arabidopsis_thaliana",
    downloadUrl:
      "https://ftp.ensemblgenomes.ebi.ac.uk/pub/plants/release-57/fasta/arabidopsis_thaliana/dna/Arabidopsis_thaliana.TAIR10.dna.toplevel.fa.gz",
    ncbiTarget: {
      label: "arabidopsis",
      database: "nt",
      entrez_query: "Arabidopsis thaliana[Organism]",
    },
  },
];

export const DOWNLOAD_PRESETS = [
  ...REFERENCE_PRESETS.filter((preset) => preset.downloadUrl).map((preset) => ({
    label: preset.label,
    url: preset.downloadUrl ?? "",
    name: preset.blastDbName,
  })),
  { label: "Custom URL...", url: "", name: "" },
];

export const DEFAULT_NCBI_TARGETS: NCBITarget[] = REFERENCE_PRESETS
  .map((preset) => preset.ncbiTarget)
  .filter((target): target is NCBITarget => Boolean(target));
