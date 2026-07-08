import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .core.config import get_settings
from .routers import annot, blast, caps, db_manager, ensembl_seq, gene_structure, jobs, primers, sequence


def _loopback(host: str | None) -> bool:
    if not host:
        return False
    value = host.lower().strip()
    if value.startswith("["):
        value = value[1:].split("]", 1)[0]
    elif value.count(":") == 1:
        value = value.rsplit(":", 1)[0]
    return value in {"127.0.0.1", "localhost", "::1", "testclient", "testserver"}


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sequence Workbench BioAPI",
        description="Local API for user-provided sequence, Primer3, and BLAST databases.",
        version="0.2.0",
    )
    settings = get_settings()
    origins = [x.strip() for x in settings.allowed_origins.split(",") if x.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def localhost_only(request: Request, call_next):
        allow_remote = os.getenv("SEQWB_ALLOW_NON_LOOPBACK", "0").lower() in {"1", "true", "yes"}
        if not allow_remote and not _loopback(request.headers.get("host")):
            return JSONResponse(status_code=403, content={"detail": "This API accepts localhost requests only."})
        return await call_next(request)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    for router in (sequence, primers, blast, caps, jobs, db_manager, annot, gene_structure, ensembl_seq):
        app.include_router(router.router)
    return app


app = create_app()
