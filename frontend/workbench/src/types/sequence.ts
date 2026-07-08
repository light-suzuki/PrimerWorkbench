// フロントエンド側で利用するシーケンス解析関連の型定義

export interface BasicAnalysisRequest {
  sequence: string;
  include_translation: boolean;
}

export interface TranslationFrameResult {
  frame: number;
  protein_sequence: string;
}

export interface BasicAnalysisResponse {
  length: number;
  gc_percent: number;
  translations: TranslationFrameResult[] | null;
}

export interface OrfAnalysisRequest {
  sequence: string;
  min_aa_length: number;
}

export interface OrfResult {
  frame: number;
  start: number;
  end: number;
  length_nt: number;
  length_aa: number;
  protein_sequence: string;
}

export interface OrfAnalysisResponse {
  orfs: OrfResult[];
}

export interface RestrictionAnalysisRequest {
  sequence: string;
  enzymes: string[];
}

export interface RestrictionCutSite {
  enzyme: string;
  cut_positions: number[];
}

export interface RestrictionAnalysisResponse {
  sequence_length: number;
  results: RestrictionCutSite[];
}

