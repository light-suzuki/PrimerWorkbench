"""
API の入出力で利用する Pydantic モデル定義。

フェーズ2では、シーケンス解析系のエンドポイントで利用する
リクエスト／レスポンスモデルを定義する。

- basic シーケンス解析
- ORF 検出
- 制限酵素サイト解析
"""

from typing import List, Optional, Literal

from pydantic import BaseModel, Field


class SequenceBasicAnalysisRequest(BaseModel):
    """basic シーケンス解析のリクエストモデル。"""

    sequence: str = Field(..., description="解析対象の塩基配列（DNA を想定）")
    include_translation: bool = Field(
        False,
        description="True の場合、3 フレームの翻訳結果も返す",
    )


class TranslationFrameResult(BaseModel):
    """翻訳フレームごとの結果。"""

    frame: int = Field(..., ge=0, le=2, description="0, 1, 2 のフレーム番号")
    protein_sequence: str = Field(..., description="翻訳されたアミノ酸配列")


class SequenceBasicAnalysisResponse(BaseModel):
    """basic シーケンス解析のレスポンスモデル。"""

    length: int = Field(..., ge=0, description="塩基配列の長さ（bp）")
    gc_percent: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="GC 含量（%）",
    )
    translations: Optional[List[TranslationFrameResult]] = Field(
        None,
        description="include_translation が True の場合のみ含まれる翻訳結果",
    )


class OrfAnalysisRequest(BaseModel):
    """ORF 解析のリクエストモデル。"""

    sequence: str = Field(..., description="ORF 検出対象の塩基配列（DNA）")
    min_aa_length: int = Field(
        50,
        ge=1,
        description="報告する ORF の最小アミノ酸長",
    )


class OrfResult(BaseModel):
    """1 つの ORF に対応する情報。"""

    frame: int = Field(..., ge=0, le=2, description="ORF が存在するフレーム（0,1,2）")
    start: int = Field(..., ge=1, description="ORF 開始位置（1-based, 塩基）")
    end: int = Field(..., ge=1, description="ORF 終了位置（1-based, 塩基, stop コドンを含む）")
    length_nt: int = Field(..., ge=0, description="ORF の長さ（塩基数）")
    length_aa: int = Field(..., ge=0, description="ORF の長さ（アミノ酸数）")
    protein_sequence: str = Field(..., description="翻訳されたアミノ酸配列（* は含まない）")


class OrfAnalysisResponse(BaseModel):
    """ORF 解析のレスポンスモデル。"""

    orfs: List[OrfResult] = Field(
        ...,
        description="検出された ORF の一覧（条件に合致しない場合は空リスト）",
    )


class RestrictionAnalysisRequest(BaseModel):
    """制限酵素サイト解析のリクエストモデル。"""

    sequence: str = Field(..., description="解析対象の塩基配列（DNA）")
    enzymes: List[str] = Field(
        ...,
        description="解析対象とする制限酵素名（例: EcoRI, BamHI）",
    )


class RestrictionCutSite(BaseModel):
    """1 つの制限酵素に対する切断サイト情報。"""

    enzyme: str = Field(..., description="制限酵素名")
    cut_positions: List[int] = Field(
        ...,
        description="切断位置（1-based, 塩基インデックス）の一覧",
    )


class RestrictionAnalysisResponse(BaseModel):
    """制限酵素サイト解析のレスポンスモデル。"""

    sequence_length: int = Field(..., ge=0, description="入力塩基配列の長さ")
    results: List[RestrictionCutSite] = Field(
        ...,
        description="各酵素ごとの切断位置情報",
    )


# -----------------------------
# プライマー設計（Primer3）
# -----------------------------


class PrimerDesignRequest(BaseModel):
    """
    プライマー設計エンドポイントへのリクエストモデル。

    - sequence: テンプレート DNA 配列
    - target_start, target_length: 増幅したい領域（1-based）の指定（任意）
    - product_size_range: Primer3 形式の product size range 文字列（例: "100-300 400-600"）
    """

    sequence: str = Field(..., description="テンプレートとなる DNA 塩基配列")
    num_return: int = Field(
        5,
        ge=1,
        le=20,
        description="返すプライマー候補ペア数（1〜20）",
    )
    target_start: Optional[int] = Field(
        None,
        ge=1,
        description="標的領域の開始位置（1-based）。target_length とセットで指定",
    )
    target_length: Optional[int] = Field(
        None,
        ge=1,
        description="標的領域の長さ（bp）。target_start とセットで指定",
    )
    product_size_range: Optional[str] = Field(
        None,
        description='増幅産物サイズ範囲（例: "100-300 400-600"）。未指定の場合は Primer3 デフォルト。',
    )
    opt_tm: float = Field(
        60.0,
        description="最適 Tm（℃）",
    )
    min_tm: float = Field(
        57.0,
        description="最小許容 Tm（℃）",
    )
    max_tm: float = Field(
        63.0,
        description="最大許容 Tm（℃）",
    )
    primer_min_size: Optional[int] = Field(
        None,
        ge=10,
        le=50,
        description="PRIMER_MIN_SIZE（未指定の場合は 18）",
    )
    primer_opt_size: Optional[int] = Field(
        None,
        ge=10,
        le=50,
        description="PRIMER_OPT_SIZE（未指定の場合は 20）",
    )
    primer_max_size: Optional[int] = Field(
        None,
        ge=10,
        le=80,
        description="PRIMER_MAX_SIZE（未指定の場合は 27）",
    )
    primer_min_gc: Optional[float] = Field(
        None,
        ge=0.0,
        le=100.0,
        description="PRIMER_MIN_GC（% , 未指定の場合は 20.0）",
    )
    primer_max_gc: Optional[float] = Field(
        None,
        ge=0.0,
        le=100.0,
        description="PRIMER_MAX_GC（% , 未指定の場合は 80.0）",
    )
    primer_salt_monovalent: Optional[float] = Field(
        None,
        ge=0.0,
        description="PRIMER_SALT_MONOVALENT（mM, 未指定の場合は 50.0）",
    )
    primer_dna_conc: Optional[float] = Field(
        None,
        ge=0.0,
        description="PRIMER_DNA_CONC（nM, 未指定の場合は 50.0）",
    )


class PrimerPair(BaseModel):
    """1 組のプライマー設計結果。"""

    index: int = Field(..., description="Primer3 による候補インデックス（0 始まり）")
    left_sequence: str = Field(..., description="左プライマー配列 (5'→3')")
    right_sequence: str = Field(..., description="右プライマー配列 (5'→3')")
    left_start: int = Field(
        ...,
        ge=1,
        description="左プライマーの開始位置（1-based）",
    )
    left_length: int = Field(..., ge=1, description="左プライマー長（bp）")
    right_start: int = Field(
        ...,
        ge=1,
        description="右プライマーの開始位置（1-based）",
    )
    right_length: int = Field(..., ge=1, description="右プライマー長（bp）")
    product_size: Optional[int] = Field(
        None,
        description="予測される PCR 産物長（bp）",
    )
    pair_penalty: Optional[float] = Field(
        None,
        description="Primer3 によるペナルティスコア（小さいほど良い）",
    )
    left_tm: Optional[float] = Field(None, description="左プライマーの Tm（℃）")
    right_tm: Optional[float] = Field(None, description="右プライマーの Tm（℃）")
    left_gc_percent: Optional[float] = Field(None, description="左プライマーの GC%")
    right_gc_percent: Optional[float] = Field(None, description="右プライマーの GC%")


class PrimerDesignResponse(BaseModel):
    """プライマー設計エンドポイントのレスポンスモデル。"""

    sequence_length: int = Field(..., ge=0, description="入力シーケンスの長さ（bp）")
    num_candidates: int = Field(..., ge=0, description="返されたプライマー候補ペア数")
    product_size_range: Optional[str] = Field(
        None,
        description="使用された product size range の文字列表現",
    )
    candidates: List[PrimerPair] = Field(
        ...,
        description="プライマー候補ペアの一覧（num_candidates 件）",
    )


# -----------------------------
# BLAST ラッパ
# -----------------------------


class BlastRequest(BaseModel):
    """
    BLAST 実行エンドポイントへのリクエストモデル。

    - sequence: クエリ配列（DNA）
    - db: makeblastdb で作成したデータベースプレフィックスパス
    - backend: 使用するエンジン（local / ncbi）
    """

    sequence: str = Field(..., description="クエリとなる DNA 塩基配列")
    db: str = Field(
        ...,
        description="BLAST データベースのプレフィックスパス（local のときのみ使用）",
    )
    backend: str = Field(
        "local",
        description='使用するエンジン: "local"（ローカル BLAST+）または "ncbi"（NCBI BLAST nt）',
    )
    local_mode: Literal["cpu", "gpu"] = Field(
        "cpu",
        description="local 使用時の実行モード（cpu/gpu）。gpu は GPU prefilter を利用する。",
    )
    ncbi_database: str = Field(
        "nt",
        description="NCBI BLAST のデータベース名（例: nt, refseq_rna, refseq_genomic）",
    )
    ncbi_entrez_query: Optional[str] = Field(
        "",
        description="NCBI BLAST の ENTREZ_QUERY。種を絞る場合に指定（例: Arabidopsis thaliana[Organism]）。",
    )
    ncbi_targets: Optional[
        list[dict]
    ] = Field(
        default=None,
        description="NCBI を複数種で並列実行する場合のターゲットリスト [{label, database?, entrez_query?}]",
    )
    ensembl_species: Optional[str] = Field(
        "homo_sapiens",
        description="Ensembl BLAST を使う場合の species 名（例: homo_sapiens, arabidopsis_thaliana など）",
    )
    task: str = Field(
        "blastn",
        description="BLAST タスク名（例: blastn, megablast など）",
    )
    engine: Literal["blast", "cuda"] = Field(
        "blast",
        description="実行エンジン（blast: 標準, cuda: 高速）",
    )
    num_threads: int | None = Field(
        None,
        ge=1,
        le=64,
        description="ローカル BLAST+ のスレッド数（省略時は CPU に応じて自動設定）",
    )
    max_hsps: int | None = Field(
        None,
        ge=1,
        le=100,
        description="各ヒットで返す HSP 数（省略時は BLAST+ デフォルト）",
    )
    evalue: float = Field(
        1e-5,
        description="E-value しきい値",
    )
    max_target_seqs: int = Field(
        25,
        ge=1,
        le=500,
        description="最大ヒット数",
    )


class BlastMultiRequest(BaseModel):
    """
    複数バックエンドで BLAST を実行するリクエストモデル。

    - backends: ["local", "ncbi", ...] のような文字列リスト
    """

    sequence: str = Field(..., description="クエリとなる DNA 塩基配列")
    db: str = Field(
        ...,
        description="ローカル BLAST+ 用のデータベースプレフィックスパス",
    )
    backends: List[str] = Field(
        default_factory=lambda: ["local", "ncbi"],
        description='実行するバックエンド一覧（例: ["local", "ncbi"]）',
    )
    local_mode: Literal["cpu", "gpu"] = Field(
        "cpu",
        description="local 使用時の実行モード（cpu/gpu）。gpu は GPU prefilter を利用する。",
    )
    ncbi_database: str = Field(
        "nt",
        description="NCBI BLAST のデータベース名（例: nt, refseq_rna, refseq_genomic）",
    )
    ncbi_entrez_query: Optional[str] = Field(
        "",
        description="NCBI BLAST の ENTREZ_QUERY。種を絞る場合に指定（例: Arabidopsis thaliana[Organism]）。",
    )
    ncbi_targets: Optional[
        list[dict]
    ] = Field(
        default=None,
        description="NCBI を複数種で並列実行する場合のターゲットリスト [{label, database?, entrez_query?}]",
    )
    task: str = Field(
        "blastn",
        description="BLAST タスク名（例: blastn, megablast など）",
    )
    engine: Literal["blast", "cuda"] = Field(
        "blast",
        description="実行エンジン（blast: 標準, cuda: 高速）",
    )
    num_threads: int | None = Field(
        None,
        ge=1,
        le=64,
        description="ローカル BLAST+ のスレッド数（省略時は CPU に応じて自動設定）",
    )
    max_hsps: int | None = Field(
        None,
        ge=1,
        le=100,
        description="各ヒットで返す HSP 数（省略時は BLAST+ デフォルト）",
    )
    evalue: float = Field(
        1e-5,
        description="E-value しきい値",
    )
    max_target_seqs: int = Field(
        25,
        ge=1,
        le=500,
        description="バックエンドごとの最大ヒット数",
    )
    ensembl_species: Optional[str] = Field(
        "homo_sapiens",
        description="Ensembl BLAST を使う場合の species 名（例: homo_sapiens, arabidopsis_thaliana など）",
    )


class BlastHitModel(BaseModel):
    """BLAST の 1 ヒット分の情報。"""

    qseqid: str
    sseqid: str
    pident: float
    length: int
    mismatch: int
    gapopen: int
    qstart: int
    qend: int
    sstart: int
    send: int
    evalue: float
    bitscore: float
    source: Optional[str] = Field(
        None,
        description='このヒットを返したバックエンド名（例: "local", "ncbi"）',
    )
    subject_title: Optional[str] = Field(
        None,
        description="ローカルDBの defline に含まれるタイトル（利用可能な場合）",
    )
    subject_chrom: Optional[str] = Field(
        None,
        description="ローカルDBの defline から推定した染色体/コンティグ名（利用可能な場合）",
    )
    subject_length: Optional[int] = Field(
        None,
        description="ローカルDBの defline から推定したシーケンス長（bp, 利用可能な場合）",
    )
    gene_ids: Optional[List[str]] = Field(
        default=None,
        description="ヒット領域と重なる gene ID（v1a GFF から判明している場合）",
    )
    gene_names: Optional[List[str]] = Field(
        default=None,
        description="ヒット領域と重なる gene 名（Name 属性など、利用可能な場合）",
    )


class BlastEquivalenceGateModel(BaseModel):
    """GPU と CPU の同等性ゲート評価結果。"""

    enabled: bool = Field(
        False,
        description="同等性ゲート判定を有効化して評価したか",
    )
    passed: bool = Field(
        True,
        description="同等性ゲートを満たしたか",
    )
    top1_match_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Top1 一致率（0.0-1.0）",
    )
    top5_overlap_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Top5 重なり率（0.0-1.0）",
    )
    bitscore_median_delta: Optional[float] = Field(
        None,
        ge=0.0,
        description="bitscore 差分絶対値の中央値",
    )
    bitscore_median_delta_ratio: Optional[float] = Field(
        None,
        ge=0.0,
        description="bitscore 差分の相対中央値（CPU bitscore 基準、0.0-1.0）",
    )
    compared_queries: int = Field(
        0,
        ge=0,
        description="比較対象クエリ数",
    )
    threshold_top1_min: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Top1 一致率の閾値",
    )
    threshold_top5_overlap_min: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Top5 重なり率の閾値",
    )
    threshold_bitscore_median_max_delta: Optional[float] = Field(
        None,
        ge=0.0,
        description="bitscore 差分中央値の許容上限",
    )
    threshold_bitscore_median_max_delta_ratio: Optional[float] = Field(
        None,
        ge=0.0,
        description="bitscore 相対差分中央値の許容上限（0.0-1.0）",
    )
    note: Optional[str] = Field(
        None,
        description="ゲート判定の補足メッセージ",
    )


class BlastResponse(BaseModel):
    """BLAST 実行結果。"""

    num_hits: int = Field(..., ge=0, description="ヒット数")
    hits: List[BlastHitModel] = Field(..., description="BLAST ヒット一覧")
    engine_requested: Optional[Literal["blast", "cuda"]] = Field(
        None,
        description="リクエストで指定された実行エンジン",
    )
    engine_executed: Optional[Literal["blast", "cuda"]] = Field(
        None,
        description="最終的に結果を返した実行エンジン",
    )
    fallback_used: Optional[bool] = Field(
        None,
        description="GPU 失敗/同等性未達により CPU へ継続したか",
    )
    fallback_reason: Optional[str] = Field(
        None,
        description="fallback が発生した理由",
    )
    equivalence_gate: Optional[BlastEquivalenceGateModel] = Field(
        None,
        description="GPU と CPU の同等性ゲート評価結果",
    )


class BlastOrRequest(BaseModel):
    """
    BLAST-OR（アラインメント表示用）のリクエストモデル。

    - local BLAST+ で 1 つの DB に対して 1 クエリを実行し、qseq/sseq を含む結果を返す。
    """

    sequence: str = Field(..., description="クエリ配列（FASTA 形式も可）")
    db: str = Field(..., description="ローカル BLAST DB（makeblastdb のプレフィックスパス）")
    program: Literal["blastn", "blastp"] = Field(
        "blastn",
        description='BLAST プログラム（"blastn" / "blastp"）。',
    )
    local_mode: Literal["cpu", "gpu"] = Field(
        "cpu",
        description="実行モード（cpu/gpu）。gpu は GPU prefilter を利用する。",
    )
    task: str = Field("megablast", description="BLAST タスク名（例: blastn, megablast など）")
    num_threads: int | None = Field(
        None,
        ge=1,
        le=64,
        description="ローカル BLAST+ のスレッド数（省略時は CPU に応じて自動設定）",
    )
    max_hsps: int | None = Field(
        None,
        ge=1,
        le=100,
        description="各ヒットで返す HSP 数（省略時は BLAST+ デフォルト）",
    )
    evalue: float = Field(1e-5, description="E-value しきい値")
    max_target_seqs: int = Field(10, ge=1, le=100, description="最大ヒット数")


class BlastOrHitModel(BlastHitModel):
    """BLAST-OR 用: qseq/sseq（ギャップ込み）を含むヒット。"""

    qseq: str = Field(..., description="アラインメント済みのクエリ配列（ギャップ含む）")
    sseq: str = Field(..., description="アラインメント済みのサブジェクト配列（ギャップ含む）")


class BlastOrResponse(BaseModel):
    """BLAST-OR 実行結果。"""

    num_hits: int = Field(..., ge=0, description="ヒット数")
    hits: List[BlastOrHitModel] = Field(..., description="BLAST-OR ヒット一覧（qseq/sseq 付き）")


class BlastBatchLocalRequest(BaseModel):
    """
    複数クエリ配列をローカル BLAST+ だけでまとめて実行するリクエスト。
    """

    sequences: List[str] = Field(..., description="クエリとなる DNA 配列の一覧")
    dbs: List[str] = Field(
        ...,
        description="ローカル BLAST+ 用のデータベースプレフィックスパス一覧（例: [/path/reference_v1, /path/sample_assembly]）",
    )
    allow_large_workload: bool = Field(
        False,
        description="True の場合、通常の batch workload 上限チェックを行わず、そのままジョブ投入する。",
    )
    local_mode: Literal["cpu", "gpu"] = Field(
        "cpu",
        description="local 使用時の実行モード（cpu/gpu）。gpu は GPU prefilter を利用する。",
    )
    task: str = Field("blastn", description="BLAST タスク名（例: blastn, megablast など）")
    engine: Literal["blast", "cuda"] = Field(
        "blast",
        description="実行エンジン（blast: 標準, cuda: 高速）",
    )
    num_threads: int | None = Field(
        None,
        ge=1,
        le=64,
        description="ローカル BLAST+ のスレッド数（省略時は CPU に応じて自動設定, 上限24）",
    )
    max_parallel_dbs: int | None = Field(
        None,
        ge=1,
        le=16,
        description="複数DBを指定した場合に同時に処理するDB数（省略時は自動）",
    )
    max_hsps: int | None = Field(
        None,
        ge=1,
        le=100,
        description="各ヒットで返す HSP 数（省略時は BLAST+ デフォルト）",
    )
    evalue: float = Field(1e-5, description="E-value しきい値")
    max_target_seqs: int = Field(
        25,
        ge=1,
        le=500,
        description="バックエンドごとの最大ヒット数",
    )


class BlastBatchLocalResponse(BaseModel):
    """
    BlastBatchLocalRequest に対するレスポンス。

    sequences[i] それぞれについて、ローカル DB 全体のヒットをまとめた BlastResponse を返す。
    """

    results: List[BlastResponse] = Field(..., description="クエリごとの BLAST 結果一覧")
    batch_meta: Optional[BlastResponse] = Field(
        None,
        description="バッチ全体の実行メタ情報（hits は空）",
    )


class BlastFetchSequenceRequest(BaseModel):
    """
    ローカル BLAST DB から配列（または部分配列）を取り出す。

    - start/end は 1-based inclusive。省略時は全長（※巨大配列は非推奨）。
    """

    db: str = Field(..., description="BLAST データベースのプレフィックスパス")
    entry: str = Field(..., description="FASTA の seqid（BLAST ヒットの sseqid の先頭トークン）")
    start: int | None = Field(None, ge=1, description="開始位置（1-based, inclusive）")
    end: int | None = Field(None, ge=1, description="終了位置（1-based, inclusive）")
    strand: str = Field("plus", description='取り出すストランド（"plus" / "minus"）')


class BlastFetchSequenceResponse(BaseModel):
    db: str
    entry: str
    start: int | None
    end: int | None
    strand: str
    length: int = Field(..., ge=0, description="返却シーケンス長（bp）")
    sequence: str = Field(..., description="塩基配列（ACGTN、改行なし）")


class BuildChromAliasesRequest(BaseModel):
    """
    染色体情報（chrN）を持たないDBに対して、参照DB（例: reference_v1）を使って
    entry→chr の対応を best-effort で推定し、キャッシュする。
    """

    db: str = Field(..., description="対象DB（makeblastdb prefix）")
    ref_db: str = Field(
        "reference_v1",
        description="参照DB（makeblastdb prefix）。例: reference_v1 / sample_assembly",
    )
    max_entries: int | None = Field(
        30,
        ge=1,
        le=200,
        description="推定対象にする entry 数（長い順に最大 N）。多数contigのDBを誤って全走査しないための上限。",
    )
    sample_bp: int = Field(
        2000,
        ge=200,
        le=20000,
        description="各 entry から抜き出して参照DBにBLASTするサンプル長（bp）",
    )
    samples_per_entry: int = Field(
        6,
        ge=1,
        le=30,
        description="各 entry から抜き出すサンプル数（均等間隔）",
    )


class LocalBlastDbInfo(BaseModel):
    """
    バックエンドが認識しているローカル BLAST DB の一覧要素。
    """

    label: str = Field(..., description="表示名（通常は DB プレフィックス名）")
    path: str = Field(..., description="makeblastdb の DB プレフィックスパス")
    has_fasta: bool = Field(..., description="元FASTA（.fa/.fasta）が存在するか")
    has_annotation: bool = Field(..., description="gene 注釈（GFF3 等）が利用可能か（推定）")
    db_type: Literal["nucl", "prot"] = Field(
        "nucl",
        description='DB の種類（"nucl" / "prot"）。',
    )


class BlastDbChromosome(BaseModel):
    chrom: str = Field(..., description='染色体ラベル（例: "chr1", "chrUn0001"）')
    entry: str = Field(..., description="DB の実体 seqid（blastdbcmd/fasta の先頭トークン）")


class BlastDbEntrySearchHit(BaseModel):
    entry: str = Field(..., description="seqid（blastdbcmd/fasta の先頭トークン）")
    title: str | None = Field(None, description="defline（利用可能な場合）")
    chrom: str | None = Field(None, description="推定染色体ラベル（利用可能な場合）")
    length: int | None = Field(None, description="配列長（bp, 利用可能な場合）")


class GeneRange(BaseModel):
    start: int = Field(..., ge=1, description="開始座標（1-based, inclusive）")
    end: int = Field(..., ge=1, description="終了座標（1-based, inclusive）")


class GeneModel(BaseModel):
    seqid: str = Field(..., description="GFF 上の seqid")
    gene_id: str = Field(..., description="gene ID（簡易正規化済み）")
    gene_name: str | None = Field(None, description="gene 名（利用可能な場合）")
    biotype: str | None = Field(None, description="gene biotype（利用可能な場合）")
    strand: int = Field(..., description="ストランド（+1 / -1）")
    start: int = Field(..., ge=1, description="gene 開始座標（1-based, inclusive）")
    end: int = Field(..., ge=1, description="gene 終了座標（1-based, inclusive）")
    exons: List[GeneRange] = Field(default_factory=list, description="exon 範囲一覧（genome 座標）")
    cds: List[GeneRange] = Field(default_factory=list, description="CDS 範囲一覧（genome 座標）")


class BlastRegionGeneModelResponse(BaseModel):
    db: str = Field(..., description="参照に使ったローカル DB")
    entry: str = Field(..., description="入力 entry（BLAST DB の seqid）")
    start: int = Field(..., ge=1, description="参照領域 start（genome 座標, 1-based）")
    end: int = Field(..., ge=1, description="参照領域 end（genome 座標, 1-based）")
    genes: List[GeneModel] = Field(default_factory=list, description="領域に重なる gene モデル")


class BlastGeneLocationQuery(BaseModel):
    db: str = Field(..., description="対象DB（makeblastdb prefix）")
    ids: List[str] = Field(..., description="gene ID / gene name の候補（複数可）")


class BlastGeneLocationItem(BaseModel):
    db: str = Field(..., description="対象DB（makeblastdb prefix）")
    input: str = Field(..., description="入力ID（そのまま）")
    normalized: str = Field(..., description="正規化後ID（末尾 .1 / -T1 など除去）")
    found: bool = Field(False, description="GFF3 から位置が引けたか")
    gene_id: str | None = Field(None, description="GFF3 上の gene ID（利用可能な場合）")
    gene_name: str | None = Field(None, description="GFF3 上の gene name（利用可能な場合）")
    seqid: str | None = Field(None, description="GFF3 上の seqid（コンティグ/染色体）")
    chrom: str | None = Field(None, description="推定染色体ラベル（chrN など、利用可能な場合）")
    start: int | None = Field(None, ge=1, description="gene 開始座標（1-based, inclusive）")
    end: int | None = Field(None, ge=1, description="gene 終了座標（1-based, inclusive）")


class BlastGeneLocationsRequest(BaseModel):
    queries: List[BlastGeneLocationQuery] = Field(..., description="db ごとの照会リスト")


# -----------------------------
# v2→v1a 変換（BLAST liftover）
# -----------------------------


class BlastLiftoverRegion(BaseModel):
    entry: str = Field(..., description="src DB の entry（seqid / chr1 など）")
    start: int = Field(..., ge=1, description="src start（1-based, inclusive）")
    end: int = Field(..., ge=1, description="src end（1-based, inclusive）")


class BlastLiftoverRequest(BaseModel):
    """
    src DB の指定領域を切り出し、dst DB に BLAST して対応領域（best hit）を返す。

    - 個人利用前提の best-effort liftover
    - まずは megablast で高速に位置当てし、gene 注釈は dst 側のローカル GFF から推定する
    """

    src_db: str = Field(..., description="source BLAST DB prefix（例: reference_v2）")
    dst_db: str = Field(..., description="destination BLAST DB prefix（例: reference_v1）")
    regions: List[BlastLiftoverRegion] = Field(..., description="変換したい src 領域の一覧")

    task: str = Field("megablast", description="dst 側に投げる blastn task（default: megablast）")
    evalue: float = Field(1e-20, description="E-value しきい値（default: 1e-20）")
    max_target_seqs: int = Field(5, ge=1, le=50, description="各クエリの最大ヒット数（default: 5）")
    max_hsps: int | None = Field(1, ge=1, le=10, description="各ヒットの最大 HSP 数（default: 1）")
    num_threads: int | None = Field(
        None,
        ge=1,
        le=64,
        description="blastn -num_threads（未指定なら CPU に応じて自動）",
    )

    max_len: int = Field(50_000, ge=1, le=200_000, description="src 切り出し最大長（default: 50000）")
    padding_bp: int = Field(0, ge=0, le=20_000, description="src 領域の左右に追加する padding（default: 0）")

    min_pident: float = Field(85.0, ge=0.0, le=100.0, description="一致度警告しきい値（default: 85.0）")
    min_coverage: float = Field(0.6, ge=0.0, le=1.0, description="カバレッジ警告しきい値（default: 0.6）")


class BlastLiftoverMapped(BaseModel):
    entry: str = Field(..., description="dst entry（seqid）")
    start: int = Field(..., ge=1, description="dst start（1-based, inclusive）")
    end: int = Field(..., ge=1, description="dst end（1-based, inclusive）")
    strand: str = Field(..., description='dst strand（"plus" / "minus"）')
    pident: float = Field(..., ge=0.0, le=100.0, description="% identity（best hit）")
    aln_len: int = Field(..., ge=0, description="alignment length（best hit）")
    coverage: float = Field(..., ge=0.0, le=1.0, description="src 切り出し長に対する alignment coverage")
    evalue: float = Field(..., description="best hit evalue")
    bitscore: float = Field(..., description="best hit bitscore")

    subject_chrom: str | None = Field(None, description="推定染色体ラベル（利用可能な場合）")
    gene_ids: List[str] | None = Field(None, description="dst 側注釈の gene_id 候補")
    gene_names: List[str] | None = Field(None, description="dst 側注釈の gene_name 候補")


class BlastLiftoverResult(BaseModel):
    src_entry: str = Field(..., description="入力 src entry")
    src_start: int = Field(..., ge=1, description="入力 src start（1-based）")
    src_end: int = Field(..., ge=1, description="入力 src end（1-based）")
    src_len: int = Field(..., ge=0, description="入力 src 長さ（bp）")

    dst: BlastLiftoverMapped | None = Field(None, description="best hit による dst 対応（見つからない場合は null）")
    note: str | None = Field(None, description="警告など（低一致度/低カバレッジ/部分整列など）")
    error: str | None = Field(None, description="エラー（失敗時）")


class BlastLiftoverResponse(BaseModel):
    src_db: str = Field(..., description="source DB")
    dst_db: str = Field(..., description="destination DB")
    results: List[BlastLiftoverResult] = Field(default_factory=list, description="入力順の変換結果")


# -----------------------------
# CAPS marker / primer design
# -----------------------------


class CapsBlastAmpliconSummary(BaseModel):
    db: str = Field(..., description="評価に使った BLAST DB ラベル（例: reference_v1）")
    amplicon_count: int = Field(..., ge=0, description="予測 PCR 産物数")
    quality: str | None = Field(None, description="簡易品質（S/A/B/C/D）")
    top_subject: str | None = Field(None, description="代表ヒットの subject（染色体/コンティグ）")
    top_start: int | None = Field(None, description="代表ヒットの開始座標（1-based）")
    top_end: int | None = Field(None, description="代表ヒットの終了座標（1-based）")
    gene_label: str | None = Field(None, description="gene 名（ローカル注釈）")


class CapsMarkerRow(BaseModel):
    index: int = Field(..., ge=1, description="1-based の行番号")
    enzyme: str = Field(..., description="制限酵素名（Biopython のクラス名）")
    primer_left: str = Field(..., description="Forward primer（5'→3'）")
    primer_right: str = Field(..., description="Reverse primer（5'→3'）")
    product_len_ref: int = Field(..., ge=0, description="参照アリルの産物長（bp）")
    product_len_alt: int = Field(..., ge=0, description="比較アリルの産物長（bp）")
    ref_product_start: int = Field(..., ge=1, description="参照ゲノム上の産物開始座標（1-based）")
    ref_product_end: int = Field(..., ge=1, description="参照ゲノム上の産物終了座標（1-based）")
    alt_product_start: int = Field(..., ge=1, description="比較ゲノム上の産物開始座標（1-based）")
    alt_product_end: int = Field(..., ge=1, description="比較ゲノム上の産物終了座標（1-based）")
    alt_strand: str = Field(..., description='比較側の向き（"plus"/"minus"）')
    mismatch_count: int = Field(..., ge=0, description="参照 vs 比較の不一致塩基数（概算）")
    cuts_ref: List[int] = Field(default_factory=list, description="参照産物での切断位置（1-based, 産物内）")
    cuts_alt: List[int] = Field(default_factory=list, description="比較産物での切断位置（1-based, 産物内）")
    fragments_ref: List[int] = Field(default_factory=list, description="参照産物の断片長（bp）")
    fragments_alt: List[int] = Field(default_factory=list, description="比較産物の断片長（bp）")
    gene_label: str | None = Field(None, description="代表 gene（ローカル注釈）")
    blast: List[CapsBlastAmpliconSummary] = Field(default_factory=list, description="ローカル BLAST による一意性評価")


class CapsDesignRequest(BaseModel):
    """
    指定したゲノム範囲で CAPS（制限酵素で共優勢判定できる）候補を大量生成する。

    - 参照側（ref_db/ref_entry/ref_start/ref_end）を基準に Primer3 で多数のプライマーペアを生成
    - 比較側（alt_db）へ BLAST で対応領域を推定し、配列差分と制限酵素切断パターン差を評価
    - 必要ならローカル BLAST DB に対して一意性（予測 PCR 産物数）も評価
    """

    ref_db: str = Field(..., description="参照ゲノムのローカル BLAST DB プレフィックスパス")
    ref_entry: str = Field(..., description="参照ゲノムの seqid（コンティグ/染色体）")
    ref_start: int = Field(..., ge=1, description="参照ゲノムの開始座標（1-based, inclusive）")
    ref_end: int = Field(..., ge=1, description="参照ゲノムの終了座標（1-based, inclusive）")

    alt_db: str = Field(..., description="比較ゲノムのローカル BLAST DB プレフィックスパス")
    map_alt_by_blast: bool = Field(True, description="比較ゲノム側の対応領域を BLAST で推定する")
    alt_entry: str | None = Field(None, description="比較ゲノムの seqid（手動指定時）")
    alt_start: int | None = Field(None, ge=1, description="比較ゲノムの開始座標（手動指定時）")
    alt_end: int | None = Field(None, ge=1, description="比較ゲノムの終了座標（手動指定時）")
    alt_strand: str = Field("plus", description='比較ゲノムの向き（手動指定時, "plus"/"minus"）')

    product_min: int = Field(200, ge=50, le=5000, description="PCR 産物の最小長（bp）")
    product_max: int = Field(800, ge=50, le=5000, description="PCR 産物の最大長（bp）")
    primer_num_return: int = Field(200, ge=1, le=2000, description="Primer3 で生成するペア数")
    max_markers: int = Field(200, ge=1, le=5000, description="返却する CAPS 候補の最大行数")

    enzymes: List[str] = Field(default_factory=list, description="使用する制限酵素名（空ならデフォルトセット）")
    enzymes_per_primer: int = Field(2, ge=1, le=20, description="1つのプライマーペアにつき返す酵素候補数")
    max_cuts_per_allele: int = Field(3, ge=0, le=20, description="産物内の最大切断回数（多すぎる酵素は除外）")
    min_fragment_len: int = Field(30, ge=1, le=500, description="最小断片長（bp）")

    require_perfect_primers_in_alt: bool = Field(
        True,
        description="比較側でもプライマー結合部が完全一致する候補のみ返す（安全重視）",
    )

    blast_check_dbs: List[str] = Field(
        default_factory=list,
        description="一意性チェック用のローカル BLAST DB プレフィックス一覧（空なら ref_db のみ）",
    )
    blast_num_threads: int | None = Field(
        None,
        ge=1,
        le=64,
        description="ローカル BLAST+ のスレッド数（省略時は自動）",
    )
    blast_max_target_seqs: int = Field(25, ge=1, le=200, description="primer BLAST の最大ヒット数")

    # Primer3 パラメータ（必要な人向け）
    opt_tm: float = Field(60.0, description="Primer3 PRIMER_OPT_TM")
    min_tm: float = Field(57.0, description="Primer3 PRIMER_MIN_TM")
    max_tm: float = Field(63.0, description="Primer3 PRIMER_MAX_TM")
    primer_min_size: int | None = Field(None, ge=10, le=60, description="Primer3 PRIMER_MIN_SIZE")
    primer_opt_size: int | None = Field(None, ge=10, le=60, description="Primer3 PRIMER_OPT_SIZE")
    primer_max_size: int | None = Field(None, ge=10, le=80, description="Primer3 PRIMER_MAX_SIZE")
    primer_min_gc: float | None = Field(None, ge=0, le=100, description="Primer3 PRIMER_MIN_GC")
    primer_max_gc: float | None = Field(None, ge=0, le=100, description="Primer3 PRIMER_MAX_GC")
    primer_salt_monovalent: float | None = Field(None, ge=0, le=500, description="Primer3 PRIMER_SALT_MONOVALENT")
    primer_dna_conc: float | None = Field(None, ge=0, le=500, description="Primer3 PRIMER_DNA_CONC")


class CapsDesignResponse(BaseModel):
    ref_db: str
    ref_entry: str
    ref_start: int
    ref_end: int
    ref_length: int

    alt_db: str
    alt_entry: str
    alt_start: int
    alt_end: int
    alt_strand: str
    alt_length: int
    mapped_by_blast: bool

    primer_pairs_generated: int = Field(..., ge=0, description="Primer3 が返したペア数")
    markers: List[CapsMarkerRow] = Field(default_factory=list, description="CAPS 候補一覧")
    warnings: List[str] = Field(default_factory=list, description="処理中の警告メッセージ")


# -----------------------------
# Jobs
# -----------------------------


class JobCreateResponse(BaseModel):
    job_id: str = Field(..., description="ジョブID")


class JobInfo(BaseModel):
    job_id: str = Field(..., description="ジョブID")
    kind: str = Field(..., description="ジョブ種別（caps_design など）")
    status: str = Field(..., description="queued/running/succeeded/failed/canceled")
    progress: float = Field(..., ge=0.0, le=1.0, description="進捗（0.0〜1.0）")
    message: str | None = Field(None, description="進捗メッセージ（任意）")
    error: str | None = Field(None, description="エラーメッセージ（失敗/キャンセル時）")
    created_at: float = Field(..., description="作成時刻（epoch seconds）")
    started_at: float | None = Field(None, description="開始時刻（epoch seconds）")
    finished_at: float | None = Field(None, description="完了時刻（epoch seconds）")
    updated_at: float = Field(..., description="最終更新時刻（epoch seconds）")


# -----------------------------
# 注釈 API
# -----------------------------


class GeneAnnotResponse(BaseModel):
    """Ensembl gene 注釈レスポンス。"""

    id: Optional[str] = Field(None, description="Ensembl Gene ID")
    display_name: Optional[str] = Field(None, description="表示名 / symbol")
    biotype: Optional[str] = Field(None, description="biotype（protein_coding など）")
    species: Optional[str] = Field(None, description="species 名")
    start: Optional[int] = Field(None, description="ゲノム上の開始座標")
    end: Optional[int] = Field(None, description="ゲノム上の終了座標")
    strand: Optional[int] = Field(None, description="ストランド（1 or -1）")
    seq_region_name: Optional[str] = Field(None, description="染色体 / コンティグ名")
    source: Optional[str] = Field(None, description="元データのソース")


class ProteinAnnotResponse(BaseModel):
    """UniProt タンパク質注釈レスポンス。"""

    accession: Optional[str] = Field(None, description="UniProt アクセッション")
    protein_name: Optional[str] = Field(None, description="推奨タンパク質名")
    gene_names: List[str] = Field(default_factory=list, description="遺伝子名の候補一覧")
    organism: Optional[str] = Field(None, description="生物種名")
    length: Optional[int] = Field(None, description="タンパク質長（aa）")
    go_terms: List[str] = Field(default_factory=list, description="関連 GO term ID 一覧")


# -----------------------------
# v1a↔v2 遺伝子ID 変換
# -----------------------------


class GeneMapConvertRequest(BaseModel):
    ids: List[str] = Field(..., description="変換したい遺伝子ID（複数可）")
    to_version: Literal["v1a", "v2"] = Field(..., description="変換先（v1a / v2）")
    from_version: Optional[Literal["auto", "v1a", "v2"]] = Field(
        "auto",
        description="変換元（auto の場合は ID から推定）",
    )


class GeneMapConvertItem(BaseModel):
    input: str = Field(..., description="入力ID（そのまま）")
    normalized: str = Field(..., description="正規化後ID（末尾 .1 など除去）")
    from_version: str | None = Field(None, description="推定/指定された変換元")
    to_version: str = Field(..., description="変換先")
    mapped: str | None = Field(None, description="変換結果（見つからない場合は null）")
    mapped_root: str | None = Field(None, description="変換結果のルートID（末尾 .1 など除去）")
    source: str | None = Field(None, description="対応表の由来（orthology / v1_vs_v2 / v2_vs_v1）")


class GeneMapInfoResponse(BaseModel):
    xlsx_path: str = Field(..., description="対応表 Excel のパス")
    meta: dict[str, int] = Field(default_factory=dict, description="読み込み統計")


class LocalDbGeneMapBuildRequest(BaseModel):
    dbs: Optional[List[str]] = Field(
        None,
        description="作成対象DB（未指定の場合は *.pep.fa のあるDBすべて）",
    )
    force: bool = Field(False, description="既存キャッシュがあっても再生成する")


class LocalDbGeneMapInfo(BaseModel):
    db: str = Field(..., description="DBラベル（accession_e など）")
    created_at: float = Field(..., description="作成時刻（epoch seconds）")
    pep_records: int = Field(..., ge=0, description="pep FASTA の配列数")
    mapped_to_v1a: int = Field(..., ge=0, description="v1a へマップできた件数")
    mapped_to_v2: int = Field(..., ge=0, description="v2 へマップできた件数")


class LocalDbGeneConvertRequest(BaseModel):
    db: str = Field(..., description="ローカルDBラベル（accession_e/accession_a/accession_d/accession_f など）")
    ids: List[str] = Field(..., description="変換したい gene ID（複数可）")


class LocalDbGeneConvertItem(BaseModel):
    input: str = Field(..., description="入力ID（そのまま）")
    normalized: str = Field(..., description="正規化後ID")
    db: str = Field(..., description="入力DB")
    v1a: str | None = Field(None, description="対応する v1a gene ID（無い場合 null）")
    v2: str | None = Field(None, description="対応する v2 gene ID（無い場合 null）")


class GeneMapConvertBetweenRequest(BaseModel):
    from_db: str = Field(..., description="変換元（v1a/v2/accession_e/accession_a/...）")
    to_db: str = Field(..., description="変換先（v1a/v2/accession_e/accession_a/...）")
    ids: List[str] = Field(..., description="変換したいID（複数可）")


class GeneMapConvertBetweenItem(BaseModel):
    input: str = Field(..., description="入力ID（そのまま）")
    normalized: str = Field(..., description="正規化後ID")
    from_db: str = Field(..., description="変換元")
    to_db: str = Field(..., description="変換先")
    v1a: str | None = Field(None, description="ハブ(v1a)のID（無い場合 null）")
    mapped: List[str] = Field(default_factory=list, description="変換先での対応ID（複数候補あり）")

# -----------------------------
# DB マネージャ
# -----------------------------


class DbRegistryItem(BaseModel):
    id: str = Field(..., description="システム内部ID (ファイル名プレフィックスとして使用)")
    name: str = Field(..., description="表示名")
    db_type: Literal["nucl", "prot"] = Field("nucl")
    source_url: Optional[str] = Field(None)
    created_at: str = Field(..., description="ISO8601")
    file_path: Optional[str] = Field(None, description="Blast DB ファイル(.nsq/.psq)への絶対パス")


class DbDownloadRequest(BaseModel):
    url: str = Field(..., description="FASTAファイルのURL (gzip対応)")
    name: str = Field(..., description="表示名 (DB Registry上の名前)")
    db_type: Literal["nucl", "prot"] = Field("nucl")


class DbIndexRequest(BaseModel):
    """既存のディレクトリをスキャンして未登録DBを登録する"""
    pass


