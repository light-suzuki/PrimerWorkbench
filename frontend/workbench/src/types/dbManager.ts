export interface DbRegistryItem {
    id: string;
    name: string;
    db_type: "nucl" | "prot";
    source_url?: string | null;
    created_at: string;
    file_path?: string | null;
}

export interface DbDownloadRequest {
    url: string;
    name: string;
    db_type: "nucl" | "prot";
}

export interface DbIndexRequest { }
