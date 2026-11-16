"""Embedded local PrimerBLAST engine. No HTTP bridge or GUI process."""
from pydantic import BaseModel, ConfigDict, Field
from pathlib import Path
from primerblast_oss.workflows import execute as run_engine


class PrimerEngineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = Field(min_length=1)
    db: list[str] = Field(min_length=1)
    product_size: str = "200-1000"
    num_return: int = Field(default=5, ge=1, le=50)
    num_threads: int = Field(default=2, ge=1, le=32)
    max_target_seqs: int = Field(default=5000, ge=1)


def design(request: PrimerEngineRequest):
    params = request.model_dump()
    params["db"] = [str(Path(db).expanduser()) for db in request.db]
    return run_engine("design", params)
