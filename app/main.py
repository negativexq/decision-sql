from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.observability.tracing import configure_tracing

configure_tracing(get_settings())

app = FastAPI(title="DecisionSQL", version="0.1.0")
app.include_router(router)
