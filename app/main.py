import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.reports import router as reports_router
from app.api.routes import router
from app.config import get_settings
from app.db.session import build_engine, build_reader_engine
from app.metadata.relationships import (
    InMemoryRelationshipStore,
    RelationshipStore,
    SqlRelationshipStore,
)
from app.metadata.secrets import SecretBox
from app.metadata.service import ConnectionService
from app.metadata.store import (
    ConnectionStore,
    InMemoryConnectionStore,
    InMemoryRunStore,
    RunStore,
    SqlConnectionStore,
    SqlRunStore,
)
from app.metadata.values import (
    InMemoryValueSharingStore,
    SqlValueSharingStore,
    ValueSharingStore,
)
from app.observability.tracing import configure_tracing
from app.operator.service import OperatorApplication, build_operator_application
from app.reports.service import ReportService
from app.reports.store import InMemoryReportStore, ReportStore, SqlReportStore

logger = logging.getLogger(__name__)
settings = get_settings()
configure_tracing(settings)

reader_engine = build_reader_engine(settings)
run_store: RunStore
connection_store: ConnectionStore
relationship_store: RelationshipStore
value_sharing_store: ValueSharingStore
report_store: ReportStore
if settings.metadata_database_url:
    metadata_engine = build_engine(settings.metadata_database_url)
    run_store = SqlRunStore(metadata_engine)
    connection_store = SqlConnectionStore(metadata_engine)
    relationship_store = SqlRelationshipStore(metadata_engine)
    value_sharing_store = SqlValueSharingStore(metadata_engine)
    report_store = SqlReportStore(metadata_engine)
else:
    logger.warning(
        "DECISION_SQL_METADATA_DATABASE_URL is not set: audit log and connections are "
        "kept in memory and lost on restart."
    )
    run_store = InMemoryRunStore()
    connection_store = InMemoryConnectionStore()
    relationship_store = InMemoryRelationshipStore()
    value_sharing_store = InMemoryValueSharingStore()
    report_store = InMemoryReportStore()

app = FastAPI(title="DecisionSQL", version="0.1.0")
operator_application: OperatorApplication = build_operator_application(
    reader_engine, settings, run_store
)
connection_service = ConnectionService(
    connection_store,
    SecretBox(settings.secret_key),
    reader_engine,
    allow_driver_install=settings.allow_driver_install,
    relationships=relationship_store,
    value_examples=settings.value_examples,
    value_sharing=value_sharing_store,
)
report_service = ReportService(report_store, operator_application, connection_service)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Decision-Actor"],
)
app.include_router(router)
app.include_router(reports_router)
