/**
 * BioAPI (FastAPI) との通信を行うクライアント。
 *
 * MVP 段階では、次のエンドポイントのみを利用する:
 * - POST /sequence/analyze/basic
 * - POST /sequence/analyze/orfs
 * - POST /sequence/analyze/restriction
 * - POST /primers/design
 * - POST /blast/run
 * - GET  /annot/gene/{id}
 * - GET  /annot/protein/{accession}
 */

import type {
  BasicAnalysisRequest,
  BasicAnalysisResponse,
  OrfAnalysisRequest,
  OrfAnalysisResponse,
  RestrictionAnalysisRequest,
  RestrictionAnalysisResponse,
} from "../types/sequence";
import type {
  PrimerDesignRequest,
  PrimerDesignResponse,
} from "../types/primers";
import type {
  BlastRequest,
  BlastResponse,
  NCBITarget,
  BlastOrRequest,
  BlastOrResponse,
  BlastBatchLocalRequest,
  BlastBatchLocalResponse,
  BlastFetchSequenceRequest,
  BlastFetchSequenceResponse,
  BuildChromAliasesRequest,
  LocalBlastDbInfo,
  BlastDbChromosome,
  BlastDbEntrySearchHit,
  RegionGeneModelResponse,
  BlastGeneLocationsRequest,
  BlastGeneLocationItem,
} from "../types/blast";
import type {
  GeneAnnotResponse,
  ProteinAnnotResponse,
} from "../types/annot";
import type { EnsemblRegionSequence } from "../types/ensembl";
import type { CapsDesignRequest, CapsDesignResponse } from "../types/caps";
import type { JobCreateResponse, JobInfo } from "../types/jobs";
import type { BlastLiftoverRequest, BlastLiftoverResponse } from "../types/convert";
import type {
  GeneMapConvertItem,
  GeneMapConvertRequest,
  GeneMapConvertBetweenItem,
  GeneMapConvertBetweenRequest,
  GeneMapInfoResponse,
  LocalDbGeneConvertItem,
  LocalDbGeneConvertRequest,
  LocalDbGeneMapBuildRequest,
  LocalDbGeneMapInfo,
} from "../types/geneMap";
import { apiGetBinary, apiGetJson, apiGetText, apiPostJson } from "./http";

// デフォルトの BioAPI ベース URL。
// - 環境変数 VITE_BIOAPI_BASE_URL があればそれを優先。
// - なければ README の標準起動手順に合わせて localhost:8000 を使う。
const DEFAULT_BIOAPI_BASE_URL =
  typeof window !== "undefined"
    ? "http://127.0.0.1:8000"
    : "http://localhost:8000";

const normalizeBaseUrl = (value: string): string => value.replace(/\/+$/, "");

const BIOAPI_BASE_URL = normalizeBaseUrl(
  (import.meta.env.VITE_BIOAPI_BASE_URL as string | undefined) ??
    DEFAULT_BIOAPI_BASE_URL,
);

export const bioapiBaseUrl = BIOAPI_BASE_URL;

async function postJson<TBody extends object, TResponse>(
  path: string,
  body: TBody,
): Promise<TResponse> {
  return apiPostJson<TBody, TResponse>(BIOAPI_BASE_URL, path, body);
}

async function getJson<TResponse>(path: string): Promise<TResponse> {
  return apiGetJson<TResponse>(BIOAPI_BASE_URL, path);
}

async function getBinary(path: string): Promise<ArrayBuffer> {
  return apiGetBinary(BIOAPI_BASE_URL, path);
}

async function getText(path: string): Promise<string> {
  return apiGetText(BIOAPI_BASE_URL, path);
}

export const bioapiClient = {
  health: (): Promise<{ status: string }> =>
    getJson("/health"),

  analyzeBasic: (body: BasicAnalysisRequest): Promise<BasicAnalysisResponse> =>
    postJson("/sequence/analyze/basic", body),

  analyzeOrfs: (body: OrfAnalysisRequest): Promise<OrfAnalysisResponse> =>
    postJson("/sequence/analyze/orfs", body),

  analyzeRestriction: (
    body: RestrictionAnalysisRequest,
  ): Promise<RestrictionAnalysisResponse> =>
    postJson("/sequence/analyze/restriction", body),

  designPrimers: (body: PrimerDesignRequest): Promise<PrimerDesignResponse> =>
    postJson("/primers/design", body),

  runBlast: (body: BlastRequest): Promise<BlastResponse> =>
    postJson("/blast/run", body),

  runBlastOr: (body: BlastOrRequest): Promise<BlastOrResponse> =>
    postJson("/blast/run_or", body),

  createBlastOrJob: (body: BlastOrRequest): Promise<JobCreateResponse> =>
    postJson("/blast/run_or_job", body),

  runBlastMulti: (body: BlastRequest & { backends: string[] }): Promise<BlastResponse> =>
    postJson("/blast/run_multi", body),

  runBlastBatchLocal: (body: BlastBatchLocalRequest): Promise<BlastBatchLocalResponse> =>
    postJson("/blast/run_batch_local", body),

  fetchLocalDbSequence: (body: BlastFetchSequenceRequest): Promise<BlastFetchSequenceResponse> =>
    postJson("/blast/fetch_sequence", body),

  listLocalBlastDbs: (dbType?: "nucl" | "prot" | "all"): Promise<LocalBlastDbInfo[]> => {
    const dt = (dbType || "nucl").trim();
    const url = dt && dt !== "nucl" ? `/blast/local_dbs?db_type=${encodeURIComponent(dt)}` : "/blast/local_dbs";
    return getJson(url);
  },

  listDbChromosomes: (db: string): Promise<BlastDbChromosome[]> =>
    getJson(`/blast/db_chromosomes?db=${encodeURIComponent(db)}`),

  searchDbEntries: (db: string, q: string, limit = 50): Promise<BlastDbEntrySearchHit[]> =>
    getJson(
      `/blast/db_search_entries?db=${encodeURIComponent(db)}&q=${encodeURIComponent(q)}&limit=${encodeURIComponent(String(limit))}`,
    ),

  fetchRegionGeneModel: (params: {
    db: string;
    entry: string;
    start: number;
    end: number;
    gene_hint?: string;
    max_genes?: number;
  }): Promise<RegionGeneModelResponse> => {
    const search = new URLSearchParams({
      db: params.db,
      entry: params.entry,
      start: String(params.start),
      end: String(params.end),
    });
    if (params.gene_hint) search.set("gene_hint", params.gene_hint);
    if (params.max_genes != null) search.set("max_genes", String(params.max_genes));
    return getJson(`/blast/region_gene_model?${search.toString()}`);
  },

  fetchGeneLocations: (body: BlastGeneLocationsRequest): Promise<BlastGeneLocationItem[]> =>
    postJson("/blast/gene_locations", body),

  fetchGeneAnnot: (geneId: string): Promise<GeneAnnotResponse> =>
    getJson(`/annot/gene/${encodeURIComponent(geneId)}`),

  fetchProteinAnnot: (
    accession: string,
  ): Promise<ProteinAnnotResponse> =>
    getJson(`/annot/protein/${encodeURIComponent(accession)}`),

  fetchEnsemblRegionSequence: (params: {
    species: string;
    chr: string;
    start: number;
    end: number;
    strand: number;
  }): Promise<EnsemblRegionSequence> => {
    const search = new URLSearchParams({
      species: params.species,
      chr: params.chr,
      start: String(params.start),
      end: String(params.end),
      strand: String(params.strand),
    });
    return getJson(`/ensembl/sequence/region?${search.toString()}`);
  },

  fetchGeneStructure: async (geneId: string, species?: string) => {
    const search = new URLSearchParams();
    if (species) search.set("species", species);
    const path =
      `/ensembl/gene_structure/${encodeURIComponent(geneId)}` +
      (search.toString() ? `?${search.toString()}` : "");
    return getJson<{
      sequence: string;
      exons: { start: number; end: number }[];
      cds: { start: number; end: number }[];
      length: number;
    }>(path);
  },

  designCapsMarkers: (body: CapsDesignRequest): Promise<CapsDesignResponse> =>
    postJson("/caps/design", body),

  createCapsDesignJob: (body: CapsDesignRequest): Promise<JobCreateResponse> =>
    postJson("/caps/design_job", body),

  createBlastBatchLocalJob: (body: BlastBatchLocalRequest): Promise<JobCreateResponse> =>
    postJson("/blast/run_batch_local_job", body),

  createBuildChromAliasesJob: (body: BuildChromAliasesRequest): Promise<JobCreateResponse> =>
    postJson("/blast/build_chrom_aliases_job", body),

  getJob: (jobId: string): Promise<JobInfo> =>
    getJson(`/jobs/${encodeURIComponent(jobId)}`),

  getJobResult: <T>(jobId: string): Promise<T> =>
    getJson(`/jobs/${encodeURIComponent(jobId)}/result`),

  fetchqueryrefMappingXlsx: (): Promise<ArrayBuffer> =>
    getBinary("/convert/query_ref_mapping_xlsx"),

  liftoverBlast: (body: BlastLiftoverRequest): Promise<BlastLiftoverResponse> =>
    postJson("/convert/liftover_blast", body),

  fetchEnsemblTranscriptExportFasta: (params: {
    species_path: string;
    transcript_id: string;
    gene_id?: string;
    region?: string;
  }): Promise<string> => {
    const search = new URLSearchParams({
      species_path: params.species_path,
      transcript_id: params.transcript_id,
    });
    if (params.gene_id) search.set("gene_id", params.gene_id);
    if (params.region) search.set("region", params.region);
    return getText(`/ensembl/export/transcript_fasta?${search.toString()}`);
  },

  cancelJob: (jobId: string): Promise<JobInfo> =>
    postJson(`/jobs/${encodeURIComponent(jobId)}/cancel`, {}),

  geneMapInfo: (): Promise<GeneMapInfoResponse> =>
    getJson("/gene_map/info"),

  convertGeneIds: (body: GeneMapConvertRequest): Promise<GeneMapConvertItem[]> =>
    postJson("/gene_map/convert", body),

  listLocalDbGeneMaps: (): Promise<LocalDbGeneMapInfo[]> =>
    getJson("/gene_map/local_db_maps"),

  listLocalDbGeneLabels: (): Promise<string[]> =>
    getJson("/gene_map/local_db_labels"),

  createBuildLocalDbGeneMapsJob: (body: LocalDbGeneMapBuildRequest): Promise<JobCreateResponse> =>
    postJson("/gene_map/build_local_db_maps_job", body),

  convertLocalDbGeneIds: (body: LocalDbGeneConvertRequest): Promise<LocalDbGeneConvertItem[]> =>
    postJson("/gene_map/convert_local", body),

  convertGeneIdsBetweenDbs: (body: GeneMapConvertBetweenRequest): Promise<GeneMapConvertBetweenItem[]> =>
    postJson("/gene_map/convert_between", body),
};
