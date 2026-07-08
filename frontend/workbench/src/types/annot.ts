// 注釈 API (/annot/...) の型定義

export interface GeneAnnotResponse {
  id: string | null;
  display_name: string | null;
  biotype: string | null;
  species: string | null;
  start: number | null;
  end: number | null;
  strand: number | null;
  seq_region_name: string | null;
  source: string | null;
}

export interface ProteinAnnotResponse {
  accession: string | null;
  protein_name: string | null;
  gene_names: string[];
  organism: string | null;
  length: number | null;
  go_terms: string[];
}

