from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import check_reader_connection, get_db
from app.observability.run_trace import RunTrace
from app.operator.models import (
    ApiHealthResponse,
    DemoPreset,
    GovernedSchemaResponse,
    PlaygroundQueryRequest,
    RunListResponse,
    RunRecord,
)
from app.operator.service import OperatorApplication

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    database: str


@router.get("/health", response_model=HealthResponse)
def health(db: Annotated[Session, Depends(get_db)]) -> HealthResponse:
    database_ok = check_reader_connection(db.get_bind())
    return HealthResponse(
        status="ok" if database_ok else "degraded",
        database="ok" if database_ok else "unavailable",
    )


def get_operator_application() -> OperatorApplication:
    """Application composition hook; tests can replace this dependency."""
    from app.main import operator_application

    return operator_application


@router.get("/api/health", response_model=ApiHealthResponse)
def operator_health(
    application: Annotated[OperatorApplication, Depends(get_operator_application)],
) -> ApiHealthResponse:
    database_ok = check_reader_connection(application.engine)
    return ApiHealthResponse(
        status="ok" if database_ok else "degraded",
        api="ok",
        database="ok" if database_ok else "unavailable",
    )


@router.get("/api/playground/presets", response_model=list[DemoPreset])
def playground_presets(
    application: Annotated[OperatorApplication, Depends(get_operator_application)],
) -> list[DemoPreset]:
    return list(application.presets)


@router.post("/api/playground/query", response_model=RunRecord)
async def playground_query(
    payload: PlaygroundQueryRequest,
    application: Annotated[OperatorApplication, Depends(get_operator_application)],
) -> RunRecord:
    if payload.preset_id is not None and payload.preset_id not in {
        preset.id for preset in application.presets
    }:
        raise HTTPException(status_code=404, detail="Unknown demo preset")
    record = await application.run(payload.question, payload.preset_id)
    return record


@router.get("/api/runs", response_model=RunListResponse)
def list_runs(
    application: Annotated[OperatorApplication, Depends(get_operator_application)],
) -> RunListResponse:
    return RunListResponse(runs=application.list_runs())


@router.get("/api/runs/{run_id}", response_model=RunRecord)
def get_run(
    run_id: str,
    application: Annotated[OperatorApplication, Depends(get_operator_application)],
) -> RunRecord:
    record = application.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return record


@router.get("/api/runs/{run_id}/trace", response_model=RunTrace)
def get_trace(
    run_id: str,
    application: Annotated[OperatorApplication, Depends(get_operator_application)],
) -> RunTrace:
    record = application.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return record.trace


@router.get("/api/schema", response_model=GovernedSchemaResponse)
def schema(
    application: Annotated[OperatorApplication, Depends(get_operator_application)],
) -> GovernedSchemaResponse:
    return application.schema()
