// query→ref 変換（BLAST liftover） API (/convert/liftover_blast) の型定義

export interface BlastLiftoverRegion {
  entry: string;
  start: number;
  end: number;
}

export interface BlastLiftoverRequest {
  src_db: string;
  dst_db: string;
  regions: BlastLiftoverRegion[];

  task?: string;
  evalue?: number;
  max_target_seqs?: number;
  max_hsps?: number | null;
  num_threads?: number | null;

  max_len?: number;
  padding_bp?: number;
  min_pident?: number;
  min_coverage?: number;
}

export interface BlastLiftoverMapped {
  entry: string;
  start: number;
  end: number;
  strand: "plus" | "minus";
  pident: number;
  aln_len: number;
  coverage: number;
  evalue: number;
  bitscore: number;
  subject_chrom?: string | null;
  gene_ids?: string[] | null;
  gene_names?: string[] | null;
}

export interface BlastLiftoverResult {
  src_entry: string;
  src_start: number;
  src_end: number;
  src_len: number;
  dst?: BlastLiftoverMapped | null;
  note?: string | null;
  error?: string | null;
}

export interface BlastLiftoverResponse {
  src_db: string;
  dst_db: string;
  results: BlastLiftoverResult[];
}

