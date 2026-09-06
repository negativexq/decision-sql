from enum import StrEnum
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GovernedMetricsMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ON = "on"


class VerifiedMemoryMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ON = "on"


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
    llm_model: str = Field(default="gpt-4o-mini", validation_alias="DECISION_SQL_LLM_MODEL")
    llm_base_url: str = Field(
        default="https://api.openai.com/v1", validation_alias="DECISION_SQL_LLM_BASE_URL"
    )
    llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DECISION_SQL_LLM_API_KEY", "OPENAI_API_KEY"),
    )
    llm_timeout_seconds: float = Field(
        default=30.0, validation_alias="DECISION_SQL_LLM_TIMEOUT_SECONDS"
    )
    llm_temperature: float | None = Field(
        default=0.0, validation_alias="DECISION_SQL_LLM_TEMPERATURE"
    )
    llm_reasoning_effort: str | None = Field(
        default=None, validation_alias="DECISION_SQL_LLM_REASONING_EFFORT"
    )
    eval_capture_model_io: bool = Field(
        default=False, validation_alias="DECISION_SQL_EVAL_CAPTURE_MODEL_IO"
    )
    schema_top_k: int = Field(default=3, validation_alias="DECISION_SQL_SCHEMA_TOP_K")
    max_context_tables: int = Field(
        default=5, validation_alias="DECISION_SQL_MAX_CONTEXT_TABLES"
    )
    max_columns_per_table: int = Field(
        default=8, validation_alias="DECISION_SQL_MAX_COLUMNS_PER_TABLE"
    )
    relationship_depth: int = Field(
        default=2, validation_alias="DECISION_SQL_RELATIONSHIP_DEPTH"
    )
    governed_metrics_mode: GovernedMetricsMode = Field(
        default=GovernedMetricsMode.OFF,
        validation_alias="DECISION_SQL_GOVERNED_METRICS_MODE",
    )
    governed_metrics_shadow_execute: bool = Field(
        default=False,
        validation_alias="DECISION_SQL_GOVERNED_METRICS_SHADOW_EXECUTE",
    )
    governed_metric_runtime_enabled: bool = Field(
        default=False,
        validation_alias="DECISION_SQL_GOVERNED_METRIC_RUNTIME_ENABLED",
    )
    verified_query_memory_mode: VerifiedMemoryMode = Field(
        default=VerifiedMemoryMode.OFF,
        validation_alias="DECISION_SQL_VERIFIED_QUERY_MEMORY_MODE",
    )
    verified_query_memory_shadow_sample_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        validation_alias="DECISION_SQL_VERIFIED_QUERY_MEMORY_SHADOW_SAMPLE_RATE",
    )
    verified_query_memory_shadow_execute: bool = Field(
        default=False,
        validation_alias="DECISION_SQL_VERIFIED_QUERY_MEMORY_SHADOW_EXECUTE",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
