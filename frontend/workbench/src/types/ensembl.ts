// Optional external region-sequence response type.

export interface EnsemblRegionSequence {
  species: string;
  chr: string;
  start: number;
  end: number;
  strand: number;
  length: number;
  seq: string;
}
