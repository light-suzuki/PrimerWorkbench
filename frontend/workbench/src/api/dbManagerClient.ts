import { bioapiBaseUrl } from "./bioapiClient";
import type { DbRegistryItem, DbDownloadRequest, DbIndexRequest } from "../types/dbManager";
import type { JobCreateResponse } from "../types/jobs";
import { apiDelete, apiGetJson, apiPostJson } from "./http";

export const dbManagerClient = {
    listDbs: (): Promise<DbRegistryItem[]> => apiGetJson(bioapiBaseUrl, "/admin/dbs"),

    downloadDb: (body: DbDownloadRequest): Promise<JobCreateResponse> =>
        apiPostJson(bioapiBaseUrl, "/admin/dbs/download", body),

    deleteDb: (dbId: string): Promise<void> =>
        apiDelete(bioapiBaseUrl, `/admin/dbs/${encodeURIComponent(dbId)}`),

    indexExisting: (): Promise<{ added: number }> =>
        apiPostJson(bioapiBaseUrl, "/admin/dbs/index_existing", {} as DbIndexRequest),
};
