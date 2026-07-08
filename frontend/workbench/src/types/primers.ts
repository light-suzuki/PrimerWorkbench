// プライマー設計 API (/primers/design) 用の型定義

export interface PrimerDesignRequest {
  sequence: string;
  num_return: number;
  target_start?: number | null;
  target_length?: number | null;
  product_size_range?: string | null;
  opt_tm: number;
  min_tm: number;
  max_tm: number;
   primer_min_size?: number;
   primer_opt_size?: number;
   primer_max_size?: number;
   primer_min_gc?: number;
   primer_max_gc?: number;
   primer_salt_monovalent?: number;
   primer_dna_conc?: number;
}

export interface PrimerPair {
  index: number;
  left_sequence: string;
  right_sequence: string;
  left_start: number;
  left_length: number;
  right_start: number;
  right_length: number;
  product_size: number | null;
  pair_penalty: number | null;
  left_tm: number | null;
  right_tm: number | null;
  left_gc_percent: number | null;
  right_gc_percent: number | null;
}

export interface PrimerDesignResponse {
  sequence_length: number;
  num_candidates: number;
  product_size_range?: string | null;
  candidates: PrimerPair[];
}
