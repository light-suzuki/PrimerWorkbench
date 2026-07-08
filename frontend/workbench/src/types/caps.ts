// CAPS primer / marker design API (/caps/design) の型定義

export interface CapsBlastAmpliconSummary {
  db: string;
  amplicon_count: number;
  quality?: string | null;
  top_subject?: string | null;
  top_start?: number | null;
  top_end?: number | null;
  gene_label?: string | null;
}

export interface CapsMarkerRow {
  index: number;
  enzyme: string;
  primer_left: string;
  primer_right: string;
  product_len_ref: number;
  product_len_alt: number;
  ref_product_start: number;
  ref_product_end: number;
  alt_product_start: number;
  alt_product_end: number;
  alt_strand: "plus" | "minus";
  mismatch_count: number;
  cuts_ref: number[];
  cuts_alt: number[];
  fragments_ref: number[];
  fragments_alt: number[];
  gene_label?: string | null;
  blast: CapsBlastAmpliconSummary[];
}

export interface CapsDesignRequest {
  ref_db: string;
  ref_entry: string;
  ref_start: number;
  ref_end: number;

  alt_db: string;
  map_alt_by_blast?: boolean;
  alt_entry?: string | null;
  alt_start?: number | null;
  alt_end?: number | null;
  alt_strand?: "plus" | "minus";

  product_min?: number;
  product_max?: number;
  primer_num_return?: number;
  max_markers?: number;

  enzymes?: string[];
  enzymes_per_primer?: number;
  max_cuts_per_allele?: number;
  min_fragment_len?: number;
  require_perfect_primers_in_alt?: boolean;

  blast_check_dbs?: string[];
  blast_num_threads?: number | null;
  blast_max_target_seqs?: number;

  opt_tm?: number;
  min_tm?: number;
  max_tm?: number;
  primer_min_size?: number | null;
  primer_opt_size?: number | null;
  primer_max_size?: number | null;
  primer_min_gc?: number | null;
  primer_max_gc?: number | null;
  primer_salt_monovalent?: number | null;
  primer_dna_conc?: number | null;
}

export interface CapsDesignResponse {
  ref_db: string;
  ref_entry: string;
  ref_start: number;
  ref_end: number;
  ref_length: number;

  alt_db: string;
  alt_entry: string;
  alt_start: number;
  alt_end: number;
  alt_strand: "plus" | "minus";
  alt_length: number;
  mapped_by_blast: boolean;

  primer_pairs_generated: number;
  markers: CapsMarkerRow[];
  warnings: string[];
}

