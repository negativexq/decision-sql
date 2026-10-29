from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import check_reader_connection, get_db
from app.metadata.connectors import (
    ConnectionConfig,
    ConnectionSummary,
    ConnectionTestResult,
    ConnectorType,
    IntrospectedTable,
)
from app.metadata.drivers.install import DriverInstallResult
from app.metadata.notes import ColumnNotesRejectedError, ColumnNotesUpdate, ConnectionNotes
from app.metadata.relationships import (
    ConnectionRelationships,
    DeclaredRelationship,
    RelationshipDeclaration,
    RelationshipRejectedError,
)
from app.metadata.secrets import SecretsUnavailableError
from app.metadata.service import (
    GOVERNED_CONNECTION_ID,
    ConnectionService,
    ConnectorUnavailableError,
    DriverInstallDisabledError,
    normalize_actor,
)
from app.metadata.store import DuplicateConnectionError
from app.metadata.values import ConnectionValues, ValueSharingRejectedError, ValueSharingUpdate
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


def get_connection_service() -> ConnectionService:
    """Composition hook for connection management; tests can replace this dependency."""
    from app.main import connection_service

    return connection_service


def get_actor(x_decision_actor: Annotated[str | None, Header()] = None) -> str:
    """Self-declared operator identity, recorded in the audit log until SSO exists."""
    return normalize_actor(x_decision_actor)


Operator = Annotated[OperatorApplication, Depends(get_operator_application)]
Connections = Annotated[ConnectionService, Depends(get_connection_service)]
Actor = Annotated[str, Depends(get_actor)]


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
    application: Operator,
    connections: Connections,
    actor: Actor,
) -> RunRecord:
    if payload.preset_id is not None and payload.preset_id not in {
        preset.id for preset in application.presets
    }:
        raise HTTPException(status_code=404, detail="Unknown demo preset")
    source = None
    if payload.connection_id != GOVERNED_CONNECTION_ID:
        if payload.preset_id is not None:
            raise HTTPException(
                status_code=400, detail="Demo scenarios run on the governed source."
            )
        try:
            source = await run_in_threadpool(connections.question_source, payload.connection_id)
        except ConnectorUnavailableError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=502, detail="Could not read the schema of this connection."
            ) from error
        if source is None:
            raise HTTPException(status_code=404, detail="Unknown connection")
    record = await application.run(payload.question, payload.preset_id, actor=actor, source=source)
    return record


@router.get("/api/runs", response_model=RunListResponse)
def list_runs(
    application: Operator,
    actor: Annotated[str | None, Query(max_length=120)] = None,
    outcome: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> RunListResponse:
    return RunListResponse(
        runs=application.list_runs(actor=actor, outcome=outcome, limit=limit),
        durable=application.run_store.durable,
    )


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


class ConnectionCreateResponse(BaseModel):
    connection: ConnectionSummary
    test: ConnectionTestResult
    saved: bool


@router.get("/api/connectors", response_model=list[ConnectorType])
def list_connectors(service: Connections) -> list[ConnectorType]:
    return list(service.connector_types())


@router.post("/api/connectors/{kind}/install", response_model=DriverInstallResult)
def install_connector_driver(kind: str, service: Connections, actor: Actor) -> DriverInstallResult:
    try:
        return service.install_driver(kind)
    except DriverInstallDisabledError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.get("/api/connections", response_model=list[ConnectionSummary])
def list_connections(service: Connections) -> list[ConnectionSummary]:
    return service.connections()


@router.post("/api/connections/test", response_model=ConnectionTestResult)
def test_connection(config: ConnectionConfig, service: Connections) -> ConnectionTestResult:
    try:
        return service.test(config)
    except ConnectorUnavailableError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/connections", response_model=ConnectionCreateResponse, status_code=201)
def create_connection(
    config: ConnectionConfig, service: Connections, actor: Actor
) -> ConnectionCreateResponse:
    try:
        summary, result = service.create(config, actor)
    except ConnectorUnavailableError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except DuplicateConnectionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SecretsUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return ConnectionCreateResponse(connection=summary, test=result, saved=bool(summary.id))


@router.post("/api/connections/{connection_id}/test", response_model=ConnectionTestResult)
def retest_connection(connection_id: str, service: Connections) -> ConnectionTestResult:
    try:
        result = service.test_saved(connection_id)
    except SecretsUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return result


@router.get("/api/connections/{connection_id}/tables", response_model=list[IntrospectedTable])
def connection_tables(connection_id: str, service: Connections) -> list[IntrospectedTable]:
    try:
        tables = service.introspect(connection_id)
    except SecretsUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:  # driver errors are reported, not leaked as 500s
        raise HTTPException(status_code=502, detail="Could not read the source schema.") from error
    if tables is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return tables


@router.get(
    "/api/connections/{connection_id}/relationships", response_model=ConnectionRelationships
)
def connection_relationships(connection_id: str, service: Connections) -> ConnectionRelationships:
    try:
        result = service.relationships(connection_id)
    except ConnectorUnavailableError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SecretsUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:  # driver errors are reported, not leaked as 500s
        raise HTTPException(status_code=502, detail="Could not read the source schema.") from error
    if result is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return result


@router.post(
    "/api/connections/{connection_id}/relationships",
    response_model=DeclaredRelationship,
    status_code=201,
)
def declare_relationship(
    connection_id: str,
    declaration: RelationshipDeclaration,
    service: Connections,
    actor: Actor,
) -> DeclaredRelationship:
    try:
        result = service.declare_relationship(connection_id, declaration, actor)
    except RelationshipRejectedError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConnectorUnavailableError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SecretsUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:  # driver errors are reported, not leaked as 500s
        raise HTTPException(status_code=502, detail="Could not read the source schema.") from error
    if result is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return result


@router.delete("/api/connections/{connection_id}/relationships/{relationship_id}", status_code=204)
def remove_relationship(connection_id: str, relationship_id: str, service: Connections) -> None:
    if not service.remove_relationship(connection_id, relationship_id):
        raise HTTPException(status_code=404, detail="Relationship not found")


@router.get("/api/connections/{connection_id}/values", response_model=ConnectionValues)
def connection_values(connection_id: str, service: Connections) -> ConnectionValues:
    try:
        result = service.values(connection_id)
    except ConnectorUnavailableError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SecretsUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:  # driver errors are reported, not leaked as 500s
        raise HTTPException(status_code=502, detail="Could not read the source schema.") from error
    if result is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return result


@router.put("/api/connections/{connection_id}/values", response_model=ConnectionValues)
def set_connection_values(
    connection_id: str, update: ValueSharingUpdate, service: Connections, actor: Actor
) -> ConnectionValues:
    try:
        result = service.set_values(connection_id, update, actor)
    except ValueSharingRejectedError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConnectorUnavailableError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SecretsUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:  # driver errors are reported, not leaked as 500s
        raise HTTPException(status_code=502, detail="Could not read the source schema.") from error
    if result is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return result


@router.get("/api/connections/{connection_id}/notes", response_model=ConnectionNotes)
def connection_notes(connection_id: str, service: Connections) -> ConnectionNotes:
    result = service.notes(connection_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return result


@router.put("/api/connections/{connection_id}/notes", response_model=ConnectionNotes)
def set_connection_notes(
    connection_id: str, update: ColumnNotesUpdate, service: Connections, actor: Actor
) -> ConnectionNotes:
    try:
        result = service.set_notes(connection_id, update, actor)
    except ColumnNotesRejectedError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConnectorUnavailableError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SecretsUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:  # driver errors are reported, not leaked as 500s
        raise HTTPException(status_code=502, detail="Could not read the source schema.") from error
    if result is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return result


@router.delete("/api/connections/{connection_id}", status_code=204)
def delete_connection(connection_id: str, service: Connections) -> None:
    if connection_id == GOVERNED_CONNECTION_ID:
        raise HTTPException(status_code=400, detail="The governed source cannot be removed here.")
    if not service.delete(connection_id):
        raise HTTPException(status_code=404, detail="Connection not found")
