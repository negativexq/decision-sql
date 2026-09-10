from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.db.session import build_reader_engine
from app.observability.tracing import configure_tracing
from app.operator.service import OperatorApplication, build_operator_application

configure_tracing(get_settings())

app = FastAPI(title="DecisionSQL", version="0.1.0")
operator_application: OperatorApplication = build_operator_application(
    build_reader_engine(get_settings()), get_settings()
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
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)
