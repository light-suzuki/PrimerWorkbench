export type GeneMapInfoResponse = {
  xlsx_path: string;
  meta: Record<string, number>;
};

export type GeneMapConvertRequest = {
  ids: string[];
  to_version: string;
  from_version?: string;
};

export type GeneMapConvertItem = {
  input: string;
  normalized: string;
  from_version: string | null;
  to_version: string;
  mapped: string | null;
  mapped_root: string | null;
  source: string | null;
};

export type LocalDbGeneMapInfo = {
  db: string;
  created_at: number;
  pep_records: number;
  mapped_to_ref: number;
  mapped_to_query: number;
};

export type LocalDbGeneMapBuildRequest = {
  dbs?: string[];
  force?: boolean;
};

export type LocalDbGeneConvertRequest = {
  db: string;
  ids: string[];
};

export type LocalDbGeneConvertItem = {
  input: string;
  normalized: string;
  db: string;
  ref: string | null;
  query: string | null;
};

export type GeneMapConvertBetweenRequest = {
  from_db: string;
  to_db: string;
  ids: string[];
};

export type GeneMapConvertBetweenItem = {
  input: string;
  normalized: string;
  from_db: string;
  to_db: string;
  ref: string | null;
  mapped: string[];
};
