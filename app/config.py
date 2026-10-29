from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(
        default="postgresql+psycopg://decision_reader:decision_reader_password@localhost:5432/decision_sql",
        validation_alias="DATABASE_URL",
    )
    admin_database_url: str = Field(
        default="postgresql+psycopg://decision_admin:decision_admin_password@localhost:5432/decision_sql",
        validation_alias="ADMIN_DATABASE_URL",
    )
    metadata_database_url: str | None = Field(
        default=None,
        validation_alias="DECISION_SQL_METADATA_DATABASE_URL",
        description="Writable store for audit records and connections; never the reader role.",
    )
    secret_key: str | None = Field(
        default=None,
        validation_alias="DECISION_SQL_SECRET_KEY",
        description="Fernet key that encrypts stored connection passwords.",
    )
    allow_driver_install: bool = Field(
        default=False,
        validation_alias="DECISION_SQL_ALLOW_DRIVER_INSTALL",
        description="Let operators install allowlisted connector drivers from the UI.",
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
    # One more model call when the runtime rejects SQL for a reason the model can fix.
    repair_enabled: bool = Field(default=True, validation_alias="DECISION_SQL_REPAIR_ENABLED")
    # When a query comes back empty, look up its text literals and tell the model so.
    result_check_enabled: bool = Field(
        default=True, validation_alias="DECISION_SQL_RESULT_CHECK_ENABLED"
    )
    value_probe_timeout_ms: int = Field(
        default=2000, validation_alias="DECISION_SQL_VALUE_PROBE_TIMEOUT_MS"
    )
    # Let the model run a few read-only probes before it decides.
    exploration_enabled: bool = Field(
        default=True, validation_alias="DECISION_SQL_EXPLORATION_ENABLED"
    )
    exploration_rounds: int = Field(
        default=2, ge=1, le=5, validation_alias="DECISION_SQL_EXPLORATION_ROUNDS"
    )
    # Show the model how categorical text columns spell their values.
    value_examples: bool = Field(default=True, validation_alias="DECISION_SQL_VALUE_EXAMPLES")
    # Refresh reports on their schedule from this process; turn off on all but one worker.
    report_scheduler: bool = Field(default=True, validation_alias="DECISION_SQL_REPORT_SCHEDULER")
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
        default=None, validation_alias="DECISION_SQL_LLM_TEMPERATURE"
    )
    llm_reasoning_effort: str | None = Field(
        default=None, validation_alias="DECISION_SQL_LLM_REASONING_EFFORT"
    )
    schema_top_k: int = Field(default=3, validation_alias="DECISION_SQL_SCHEMA_TOP_K")
    max_context_tables: int = Field(default=5, validation_alias="DECISION_SQL_MAX_CONTEXT_TABLES")
    max_columns_per_table: int = Field(
        default=8, validation_alias="DECISION_SQL_MAX_COLUMNS_PER_TABLE"
    )
    relationship_depth: int = Field(default=2, validation_alias="DECISION_SQL_RELATIONSHIP_DEPTH")
    full_context_max_tables: int = Field(
        default=12, ge=0, validation_alias="DECISION_SQL_FULL_CONTEXT_MAX_TABLES"
    )
    # Tables one relationship away from the selected ones, added beyond max_context_tables.
    context_neighbor_tables: int = Field(
        default=6, ge=0, validation_alias="DECISION_SQL_CONTEXT_NEIGHBOR_TABLES"
    )
    # One more model call with a table the model named from the omitted list.
    context_expansion_enabled: bool = Field(
        default=True, validation_alias="DECISION_SQL_CONTEXT_EXPANSION_ENABLED"
    )
    # How the governed context reaches the model: canonical JSON, or the same facts as
    # compact text (one line per column, names instead of ids).
    context_format: Literal["json", "compact"] = Field(
        default="compact", validation_alias="DECISION_SQL_CONTEXT_FORMAT"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
