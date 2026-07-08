// BLAST ラッパ API (/blast/run) の型定義

export interface BlastRequest {
  sequence: string;
  db: string;
  local_mode?: "cpu" | "gpu";
  task?: string;
  evalue?: number;
  max_target_seqs?: number;
  max_hsps?: number;
  num_threads?: number;
  backend?: string;
  ensembl_species?: string;
  ncbi_database?: string;
  ncbi_entrez_query?: string;
  engine?: "blast" | "cuda";
  ncbi_targets?: NCBITarget[];
}

export interface NCBITarget {
  label: string;
  database?: string;
  entrez_query?: string;
  engine?: "blast" | "cuda";
}

export interface BlastHit {
  qseqid: string;
  sseqid: string;
  pident: number;
  length: number;
  mismatch: number;
  gapopen: number;
  qstart: number;
  qend: number;
  sstart: number;
  send: number;
  evalue: number;
  bitscore: number;
  source?: string;
  subject_title?: string;
  subject_chrom?: string;
  subject_length?: number;
  gene_ids?: string[];
  gene_names?: string[];
}

export interface BlastResponse {
  num_hits: number;
  hits: BlastHit[];
}

// BLAST-OR (alignment viewer) types: /blast/run_or
export interface BlastOrRequest {
  sequence: string;
  db: string;
  program?: "blastn" | "blastp";
  local_mode?: "cpu" | "gpu";
  task?: string;
  num_threads?: number;
  max_hsps?: number;
  evalue?: number;
  max_target_seqs?: number;
}

export interface BlastOrHit extends BlastHit {
  qseq: string;
  sseq: string;
}

export interface BlastOrResponse {
  num_hits: number;
  hits: BlastOrHit[];
}

export interface BlastBatchLocalRequest {
  sequences: string[];
  dbs: string[];
  local_mode?: "cpu" | "gpu";
  engine?: "blast" | "cuda";
  task?: string;
  evalue?: number;
  max_target_seqs?: number;
  max_hsps?: number;
  num_threads?: number;
  max_parallel_dbs?: number;
}

export interface BlastBatchLocalResponse {
  results: BlastResponse[];
}

export interface BlastFetchSequenceRequest {
  db: string;
  entry: string;
  start?: number;
  end?: number;
  strand?: "plus" | "minus";
}

export interface BlastFetchSequenceResponse {
  db: string;
  entry: string;
  start?: number;
  end?: number;
  strand: "plus" | "minus";
  length: number;
  sequence: string;
}

export interface BuildChromAliasesRequest {
  db: string;
  ref_db?: string;
  max_entries?: number;
  sample_bp?: number;
  samples_per_entry?: number;
}

export interface LocalBlastDbInfo {
  label: string;
  path: string;
  has_fasta: boolean;
  has_annotation: boolean;
  db_type?: "nucl" | "prot";
}

export interface BlastDbChromosome {
  chrom: string;
  entry: string;
}

export interface BlastDbEntrySearchHit {
  entry: string;
  title?: string | null;
  chrom?: string | null;
  length?: number | null;
}

export interface GeneModelRange {
  start: number; // 1-based inclusive (genome)
  end: number; // 1-based inclusive (genome)
}

export interface RegionGeneModelGene {
  seqid: string;
  gene_id: string;
  gene_name?: string | null;
  biotype?: string | null;
  strand: number; // +1 / -1
  start: number;
  end: number;
  exons: GeneModelRange[];
  cds: GeneModelRange[];
}

export interface RegionGeneModelResponse {
  db: string;
  entry: string;
  start: number;
  end: number;
  genes: RegionGeneModelGene[];
}

// /blast/gene_locations
export interface BlastGeneLocationQuery {
  db: string;
  ids: string[];
}

export interface BlastGeneLocationItem {
  db: string;
  input: string;
  normalized: string;
  found: boolean;
  gene_id?: string | null;
  gene_name?: string | null;
  seqid?: string | null;
  chrom?: string | null;
  start?: number | null;
  end?: number | null;
}

export interface BlastGeneLocationsRequest {
  queries: BlastGeneLocationQuery[];
}
