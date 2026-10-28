import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.db.session import build_engine, build_reader_engine
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
from app.observability.tracing import configure_tracing
from app.operator.service import OperatorApplication, build_operator_application

logger = logging.getLogger(__name__)
settings = get_settings()
configure_tracing(settings)

reader_engine = build_reader_engine(settings)
run_store: RunStore
connection_store: ConnectionStore
if settings.metadata_database_url:
    metadata_engine = build_engine(settings.metadata_database_url)
    run_store = SqlRunStore(metadata_engine)
    connection_store = SqlConnectionStore(metadata_engine)
else:
    logger.warning(
        "DECISION_SQL_METADATA_DATABASE_URL is not set: audit log and connections are "
        "kept in memory and lost on restart."
    )
    run_store = InMemoryRunStore()
    connection_store = InMemoryConnectionStore()

app = FastAPI(title="DecisionSQL", version="0.1.0")
operator_application: OperatorApplication = build_operator_application(
    reader_engine, settings, run_store
)
connection_service = ConnectionService(
    connection_store,
    SecretBox(settings.secret_key),
    reader_engine,
    allow_driver_install=settings.allow_driver_install,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Decision-Actor"],
)
app.include_router(router)
