from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = Field(default="local", validation_alias="APP_ENV")
    database_url: str = Field(
        default="postgresql+psycopg://decision_reader:decision_reader_password@localhost:5432/decision_sql",
        validation_alias="DATABASE_URL",
    )
    admin_database_url: str = Field(
        default="postgresql+psycopg://decision_admin:decision_admin_password@localhost:5432/decision_sql",
        validation_alias="ADMIN_DATABASE_URL",
    )
    otel_service_name: str = Field(default="decision-sql", validation_alias="OTEL_SERVICE_NAME")
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None, validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    max_plan_rows: int = Field(default=100_000, validation_alias="DECISION_SQL_MAX_PLAN_ROWS")
    max_plan_cost: float = Field(default=100_000.0, validation_alias="DECISION_SQL_MAX_PLAN_COST")
    max_result_rows: int = Field(default=1000, validation_alias="DECISION_SQL_MAX_RESULT_ROWS")
    statement_timeout_ms: int = Field(
        default=5000, validation_alias="DECISION_SQL_STATEMENT_TIMEOUT_MS"
    )
    reader_role: str = Field(default="decision_reader", validation_alias="DECISION_SQL_READER_ROLE")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
