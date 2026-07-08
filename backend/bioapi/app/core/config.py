from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    アプリケーション共通設定。

    今後、以下のような設定値を追加していく想定：
    - BioAPI が待ち受けるホスト・ポート
    - BLAST データベースのパス
    - Primer3 実行ファイルのパス
    - 外部 API（Ensembl / UniProt など）のベース URL
    """

    app_name: str = "BioAPI"
    # NCBI の API キー（環境変数または .env から取得）
    ncbi_api_key: str | None = None
    # Ensembl REST のベース URL（Plants ミラーを使う場合はここを書き換える）
    ensembl_rest_base_url: str = "https://rest.ensembl.org"
    # CORS で許可するオリジン（カンマ区切りの文字列）
    allowed_origins: str = (
        "http://127.0.0.1:5173,http://localhost:5173,"
        "http://127.0.0.1:3000,http://localhost:3000"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


@lru_cache()
def get_settings() -> Settings:
    """
    設定オブジェクトをシングルトン的に取得するためのヘルパー。

    FastAPI の依存性注入と組み合わせて利用することを想定している。
    """
    return Settings()
