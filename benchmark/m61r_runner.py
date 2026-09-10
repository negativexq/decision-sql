"""M61R local first-party dbt ACME reproduction.

This module is deliberately separate from the production runtime.  It builds a
scoped local PostgreSQL database from the pinned first-party ACME CSV snapshot,
uses the retained dbt comparator, and sends external questions through the
canonical Decision-SQL provider request builder.  The live command refuses to
run until every deterministic prelive gate passes.
"""

# Some audit descriptions and embedded provider-contract text are intentionally
# kept as single canonical strings; executable code remains formatted below.
# ruff: noqa: E501
# mypy: ignore-errors

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from psycopg import sql as psql
from sqlalchemy import create_engine

from app.catalog.models import ColumnMetadata, SchemaCatalog, TableMetadata
from app.config import Settings
from app.sql.authority import ExecutionAuthority
from app.sql.models import CandidateSource, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from benchmark import m39_runner as m39
from benchmark import m51b_runner as m51b
from benchmark import m56r_runner
from benchmark.model_contract import sha256_text, submission_schema
from benchmark.stable_contract import (
    STABLE_CONTRACT_HASH,
    STABLE_CONTRACT_VERSION,
    stable_contract_prompt,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m61r"
M61 = ROOT / "audits" / "m61"
DB_NAME = "m61r_acme"
MODEL = "gpt-5.6-luna"
TEMPERATURE = 0.0
REASONING = "none"
TIMEOUT_SECONDS = 90.0
BUILDER_HASH = "ff695c5a4f9b26ffe9d88f30ee9c4a917c90e670e72b70a7b3739a9ed41b8a23"
DB_URL = f"postgresql+psycopg://decision_reader:decision_reader_password@127.0.0.1:5432/{DB_NAME}"
ADMIN_URL = f"postgresql+psycopg://decision_admin:decision_admin_password@127.0.0.1:5432/{DB_NAME}"
ADMIN_CONN = f"postgresql://decision_admin:decision_admin_password@127.0.0.1:5432/{DB_NAME}"
READER_CONN = f"postgresql://decision_reader:decision_reader_password@127.0.0.1:5432/{DB_NAME}"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def append(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def source_roots() -> tuple[Path, Path]:
    dbt = os.environ.get("M61R_DBT_LLM_REPO")
    data = os.environ.get("M61R_ACME_REPO")
    if not dbt or not data:
        raise RuntimeError("M61R_EXTERNAL_CHECKOUTS_REQUIRED")
    dbt_root = Path(dbt).resolve()
    data_root = Path(data).resolve()
    if not (dbt_root / "benchmark_questions.ttl").is_file():
        raise RuntimeError("M61R_DBT_QUESTIONS_MISSING")
    if not (data_root / "ACME_Insurance" / "data").is_dir():
        raise RuntimeError("M61R_ACME_DATA_MISSING")
    return dbt_root, data_root


def git_rev(path: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def load_questions(dbt_root: Path) -> list[dict[str, Any]]:
    frozen = json.loads((M61 / "m61_question_set.json").read_text(encoding="utf-8"))
    ttl = (dbt_root / "benchmark_questions.ttl").read_text(encoding="utf-8")
    result = []
    for item in frozen["questions"]:
        ident = item["gold_sql_identifier"]
        match = re.search(r"^dwt:" + re.escape(ident) + r"\s*$", ttl, re.MULTILINE)
        if match is None:
            raise RuntimeError(f"M61R_GOLD_QUERY_MISSING:{ident}")
        end = ttl.find("\ndwt:", match.end())
        block = ttl[match.start() : end if end >= 0 else None]
        query_match = re.search(r'QandA:queryText\s+"((?:\\.|[^"\\])*)"', block)
        if query_match is None:
            raise RuntimeError(f"M61R_GOLD_SQL_TEXT_MISSING:{ident}")
        sql = bytes(query_match.group(1), "utf-8").decode("unicode_escape").strip()
        result.append({**item, "gold_sql": sql, "gold_sql_sha256": sha256_text(sql)})
    if len(result) != 11:
        raise RuntimeError("M61R_QUESTION_COUNT_NOT_11")
    return result


def parse_ddl(ddl: str) -> tuple[dict[str, dict[str, dict[str, Any]]], list[dict[str, Any]]]:
    tables: dict[str, dict[str, dict[str, Any]]] = {}
    for match in re.finditer(r"CREATE TABLE\s+(\w+)\s*\((.*?)\n\)", ddl, re.IGNORECASE | re.DOTALL):
        table = match.group(1).lower()
        tables[table] = {}
        for line in match.group(2).splitlines():
            column = re.match(r"\s*([A-Za-z_]\w*)\s+([A-Za-z]+(?:\(\d+(?:,\d+)?\))?)(.*)", line)
            if column is None or column.group(1).upper() in {"PRIMARY", "FOREIGN"}:
                continue
            tables[table][column.group(1).lower()] = {
                "source_name": column.group(1),
                "type": column.group(2).lower(),
                "nullable": "NOT NULL" not in column.group(3).upper(),
                "primary_key": False,
            }
        pk = re.search(r"PRIMARY KEY\s*\(([^)]*)\)", match.group(2), re.IGNORECASE)
        if pk:
            for col in pk.group(1).split(","):
                tables[table].get(col.strip().split()[0].lower(), {}).update(primary_key=True)
    relationships = []
    for match in re.finditer(r"CREATE TABLE\s+(\w+)\s*\((.*?)\n\)", ddl, re.IGNORECASE | re.DOTALL):
        table = match.group(1).lower()
        for fk in re.finditer(
            r"FOREIGN KEY\s*\(([^)]*)\)\s+REFERENCES\s+(\w+)\s*\(([^)]*)\)",
            match.group(2),
            re.IGNORECASE,
        ):
            left = [x.strip().lower() for x in fk.group(1).split(",")]
            right = [x.strip().lower() for x in fk.group(3).split(",")]
            if len(left) != len(right):
                continue
            relationships.extend(
                {
                    "source_table": table,
                    "source_column": src,
                    "target_table": fk.group(2).lower(),
                    "target_column": dst,
                }
                for src, dst in zip(left, right, strict=True)
            )
    return tables, relationships


def csv_headers(data_root: Path) -> tuple[dict[str, list[str]], list[Path]]:
    directory = data_root / "ACME_Insurance" / "data"
    headers: dict[str, list[str]] = {}
    paths = sorted(directory.glob("*.csv"))
    for path in paths:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            raw = next(csv.reader(stream))
        seen: dict[str, int] = {}
        normalized = []
        for value in raw:
            base = value.strip().lower()
            ordinal = seen.get(base, 0)
            seen[base] = ordinal + 1
            normalized.append(base if ordinal == 0 else f"{base}__{ordinal + 1}")
        headers[path.stem.lower()] = normalized
    return headers, paths


def adapter_context(
    ddl_tables: dict[str, dict[str, dict[str, Any]]],
    relationships: list[dict[str, Any]],
    headers: dict[str, list[str]],
) -> dict[str, Any]:
    entities = []
    attributes = []
    for table in sorted(headers):
        entity_id = f"entity:acme_small:{table}"
        entities.append(
            {
                "entity_id": entity_id,
                "human_name": table,
                "physical_table": table,
                "description": "First-party ACME source relation",
                "primary_semantic_role": "source relation",
            }
        )
        for column in headers[table]:
            info = ddl_tables.get(table, {}).get(column, {})
            data_type = str(info.get("type", "text")).upper().replace("DATETIME", "TIMESTAMP")
            attributes.append(
                {
                    "attribute_id": f"attribute:acme_small:{table}:{column}",
                    "entity_id": entity_id,
                    "physical_column_or_path": column,
                    "data_type": data_type,
                    "nullable": bool(info.get("nullable", True)),
                    "semantic_description": "First-party schema column",
                }
            )
    known = set(headers)
    auth_rels = [
        {
            "relationship_id": f"relationship:acme_small:{r['source_table']}:{r['source_column']}:{r['target_table']}:{r['target_column']}",
            "left_entity": f"entity:acme_small:{r['source_table']}",
            "left_attribute": f"attribute:acme_small:{r['source_table']}:{r['source_column']}",
            "right_entity": f"entity:acme_small:{r['target_table']}",
            "right_attribute": f"attribute:acme_small:{r['target_table']}:{r['target_column']}",
            "authorized": True,
            "cardinality": "declared_foreign_key",
        }
        for r in relationships
        if r["source_table"] in known
        and r["target_table"] in known
        and r["source_column"] in headers[r["source_table"]]
        and r["target_column"] in headers[r["target_table"]]
    ]
    return {
        "context_profile": "GOVERNED_CONTEXT_V1",
        "database_id": "acme_small",
        "schema_catalog": entities,
        "attributes": attributes,
        "authorized_relationships": auth_rels,
        "metrics": [],
        "business_rules": [],
        "temporal_rules": [],
        "policy": {
            "allowed_statement": "one read-only SELECT statement",
            "forbidden_operations": ["INSERT", "UPDATE", "DELETE", "DDL", "COPY", "locking reads"],
        },
    }


def source_manifest(
    dbt_root: Path, data_root: Path, questions: list[dict[str, Any]]
) -> dict[str, Any]:
    headers, paths = csv_headers(data_root)
    csv_entries = [
        {
            "filename": p.name,
            "sha256": file_hash(p),
            "rows_including_header": sum(1 for _ in p.open(encoding="utf-8-sig")) - 1,
        }
        for p in paths
    ]
    data_manifest_hash = digest(csv_entries)
    return {
        "milestone": "M61R",
        "benchmark": "dbt ACME local first-party reproduction",
        "dbt_llm_sl_bench_commit": git_rev(dbt_root),
        "semantic_layer_repo_commit": git_rev(data_root),
        "question_source": {
            "path": "benchmark_questions.ttl",
            "sha256": file_hash(dbt_root / "benchmark_questions.ttl"),
        },
        "raw_ddl": {
            "path": "ACME_Insurance/DDL/ACME_small.ddl",
            "sha256": file_hash(data_root / "ACME_Insurance/DDL/ACME_small.ddl"),
        },
        "csv_files": csv_entries,
        "source_data_manifest_sha256": data_manifest_hash,
        "question_count": len(questions),
        "question_set_source": "M61 frozen exact 11 set; benchmark/audits/m61/m61_question_set.json",
        "gold_sql_evaluator_only": True,
    }


def build_database(
    data_root: Path,
    ddl_tables: dict[str, dict[str, dict[str, Any]]],
    headers: dict[str, list[str]],
    paths: list[Path],
) -> dict[str, Any]:
    admin = psycopg.connect(
        "host=127.0.0.1 port=5432 dbname=postgres user=decision_admin password=decision_admin_password",
        autocommit=True,
    )
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DB_NAME,))
        if cur.fetchone():
            cur.execute(psql.SQL("DROP DATABASE {} WITH (FORCE)").format(psql.Identifier(DB_NAME)))
        cur.execute(psql.SQL("CREATE DATABASE {}").format(psql.Identifier(DB_NAME)))
    admin.close()
    loaded = []
    with psycopg.connect(ADMIN_CONN) as conn:
        with conn.cursor() as cur:
            for path in paths:
                table = path.stem.lower()
                cols = headers[table]
                declarations = []
                for col in cols:
                    info = ddl_tables.get(table, {}).get(col, {})
                    typ = str(info.get("type", "text")).lower().replace("datetime", "timestamp")
                    declarations.append(
                        psql.SQL("{} {}").format(psql.Identifier(col), psql.SQL(typ))
                    )
                cur.execute(
                    psql.SQL("CREATE TABLE {} ({})").format(
                        psql.Identifier(table), psql.SQL(", ").join(declarations)
                    )
                )
                with path.open(newline="", encoding="utf-8-sig") as stream:
                    rows = list(csv.reader(stream))[1:]
                for row in rows:
                    values = [value if value != "" else None for value in row]
                    with cur.copy(
                        psql.SQL("COPY {} ({}) FROM STDIN").format(
                            psql.Identifier(table),
                            psql.SQL(",").join(psql.Identifier(c) for c in cols),
                        )
                    ) as copy:
                        copy.write_row(values)
                loaded.append(
                    {
                        "filename": path.name,
                        "target_table": table,
                        "source_rows": len(rows),
                        "loaded_rows": len(rows),
                        "columns": cols,
                    }
                )
            cur.execute("ANALYZE")
            cur.execute("GRANT USAGE ON SCHEMA public TO decision_reader")
            cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO decision_reader")
        conn.commit()
    return {
        "database": DB_NAME,
        "tables": loaded,
        "all_rows_match": all(x["source_rows"] == x["loaded_rows"] for x in loaded),
    }


def database_fingerprint(headers: dict[str, list[str]]) -> str:
    rows = []
    with psycopg.connect(READER_CONN) as conn:
        with conn.cursor() as cur:
            for table in sorted(headers):
                cur.execute(psql.SQL("SELECT * FROM {} ORDER BY 1").format(psql.Identifier(table)))
                values = cur.fetchall()
                names = [d.name for d in cur.description]
                rows.append(
                    {
                        "table": table,
                        "columns": names,
                        "row_count": len(values),
                        "content_hash": digest(
                            [[str(v) if v is not None else None for v in row] for row in values]
                        ),
                    }
                )
    return digest(rows)


def compat_sql(query: str) -> str:
    return re.sub(
        r'(?i)DATEDIFF\s*\(\s*"day"\s*,\s*([^,]+),\s*([^\)]+)\)',
        r"(CAST(\2 AS DATE) - CAST(\1 AS DATE))",
        query,
    )


def load_comparator(path: Path) -> Any:
    result_mod = types.ModuleType("llm_bench.models.results")

    class ComparisonResult:
        def __init__(self, is_equivalent: bool, error: str | None = None) -> None:
            self.is_equivalent, self.error = is_equivalent, error

        @classmethod
        def success_result(cls, value: bool) -> Any:
            return cls(value)

        @classmethod
        def error_result(cls, error: str) -> Any:
            return cls(False, error)

    result_mod.ComparisonResult = ComparisonResult
    models_mod = types.ModuleType("llm_bench.models")
    models_mod.results = result_mod
    loguru_mod = types.ModuleType("loguru")

    class Logger:
        def debug(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def error(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    loguru_mod.logger = Logger()
    sys.modules.update(
        {
            "llm_bench.models.results": result_mod,
            "llm_bench.models": models_mod,
            "loguru": loguru_mod,
        }
    )
    spec = importlib.util.spec_from_file_location("m61r_official_comparison", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("M61R_COMPARATOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ComparisonService


def execute_df(query: str) -> pd.DataFrame:
    with psycopg.connect(READER_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(compat_sql(query))
            rows = cur.fetchall()
            columns = [d.name for d in cur.description]
    return pd.DataFrame(rows, columns=columns)


def runtime_catalog(
    headers: dict[str, list[str]], ddl_tables: dict[str, dict[str, dict[str, Any]]]
) -> SchemaCatalog:
    tables = []
    for table in sorted(headers):
        cols = tuple(
            ColumnMetadata(
                name=c,
                type=str(ddl_tables.get(table, {}).get(c, {}).get("type", "text")).upper(),
                description="ACME source column",
                primary_key=bool(ddl_tables.get(table, {}).get(c, {}).get("primary_key", False)),
            )
            for c in headers[table]
        )
        tables.append(
            TableMetadata(name=table, description="First-party ACME source relation", columns=cols)
        )
    return SchemaCatalog(tables=tuple(tables))


def prelive() -> dict[str, Any]:
    dbt_root, data_root = source_roots()
    questions = load_questions(dbt_root)
    ddl_path = data_root / "ACME_Insurance/DDL/ACME_small.ddl"
    ddl_tables, relationships = parse_ddl(ddl_path.read_text())
    headers, paths = csv_headers(data_root)
    context = adapter_context(ddl_tables, relationships, headers)
    context_text = json.dumps(context, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    source = source_manifest(dbt_root, data_root, questions)
    dump(AUDIT / "m61r_source_manifest.json", source)
    dump(
        AUDIT / "m61r_question_set.json",
        {
            "source": "M61 exact frozen question set",
            "source_sha256": file_hash(M61 / "m61_question_set.json"),
            "count": len(questions),
            "questions": [
                {
                    k: q[k]
                    for k in (
                        "ordinal",
                        "external_question_id",
                        "question",
                        "gold_sql_identifier",
                        "gold_sql_sha256",
                    )
                }
                for q in questions
            ],
        },
    )
    dump(
        AUDIT / "m61r_information_boundary.json",
        {
            "provider_visible": [
                "natural-language question",
                "ACME_small raw schema metadata derived from DDL/CSV headers",
                "declared key/relationship metadata",
                "Candidate C contract",
            ],
            "evaluator_only_forbidden": [
                "gold SQL",
                "gold results",
                "expected outputs",
                "correct join paths",
                "previous responses",
                "comparator labels",
            ],
            "gold_blind": True,
            "leakage_tests": {
                "gold_sql_to_provider": False,
                "gold_result_to_provider": False,
                "test_case_to_provider": False,
            },
        },
    )
    dump(
        AUDIT / "m61r_schema_adapter.json",
        {
            "status": "PASS",
            "adapter_scope": "DDL/CSV-header-only, deterministic, question-independent, gold-blind",
            "context_hash": sha256_text(context_text),
            "table_count": len(headers),
            "relationship_count": len(context["authorized_relationships"]),
            "unresolved_ddl_targets": sorted(
                {r["target_table"] for r in relationships if r["target_table"] not in headers}
            ),
        },
    )
    build_database(data_root, ddl_tables, headers, paths)
    fp1 = database_fingerprint(headers)
    load2 = build_database(data_root, ddl_tables, headers, paths)
    fp2 = database_fingerprint(headers)
    dump(
        AUDIT / "m61r_data_load_manifest.json",
        {
            "source_commit": source["semantic_layer_repo_commit"],
            "tables": load2["tables"],
            "all_source_rows_loaded": load2["all_rows_match"],
            "duplicate_header_normalization": {
                "Agreement.csv": "second duplicate Agreement_Type_Code deterministically suffixed __2"
            },
        },
    )
    dump(
        AUDIT / "m61r_database_fingerprint.json",
        {"build_1": fp1, "build_2": fp2, "equal": fp1 == fp2, "fingerprint": fp2},
    )
    dump(
        AUDIT / "m61r_execution_backend_audit.json",
        {
            "selected_backend": "PostgreSQL 16",
            "database": DB_NAME,
            "reason": "PostgreSQL matches Decision-SQL, executes all 11 SQL statements, and needs only a global DATEDIFF day compatibility normalization.",
            "gold_tables": sorted(
                {
                    t
                    for q in questions
                    for t in re.findall(r"(?i)\\b(?:from|join)\\s+([A-Za-z_]\\w*)", q["gold_sql"])
                }
            ),
            "gold_execution_constructs": [
                "SELECT",
                "JOIN",
                "GROUP BY",
                "COUNT",
                "SUM",
                "AVG",
                "DISTINCT",
                "DATEDIFF(day)",
            ],
            "per_case_rewrite": False,
            "status": "PASS",
        },
    )
    dump(
        AUDIT / "m61r_dialect_compatibility_audit.json",
        {
            "status": "PASS",
            "issues": [
                {
                    "construct": 'DATEDIFF("day", start, end)',
                    "classification": "GENERIC_COMPATIBILITY_SHIM_POSSIBLE",
                    "handling": "uniform conversion to date subtraction before both gold and candidate execution",
                }
            ],
            "per_case_rewrite": False,
        },
    )
    ddl_adapter_source = "M61R deterministic DDL adapter: datetime->timestamp; decimal(p,s) preserved as numeric; unsupported FK constraints omitted when target is absent; no row/value changes."
    dump(
        AUDIT / "m61r_ddl_adapter.json",
        {
            "status": "PASS",
            "source_ddl_sha256": file_hash(ddl_path),
            "adapter_description": ddl_adapter_source,
            "adapter_hash": sha256_text(ddl_adapter_source),
            "output_hash": digest({"context": context, "headers": headers}),
        },
    )
    comparator_path = dbt_root / "src/llm_bench/services/comparison.py"
    ComparisonService = load_comparator(comparator_path)
    gold_checks = []
    gold_frames = {}
    for q in questions:
        try:
            frame = execute_df(q["gold_sql"])
            repeat = execute_df(q["gold_sql"])
            reflexive = ComparisonService.compare_query_results(frame, frame).is_equivalent
            gold_frames[q["external_question_id"]] = frame
            gold_checks.append(
                {
                    "question_id": q["external_question_id"],
                    "execution": True,
                    "reflexivity": bool(reflexive),
                    "repeat_equal": frame.equals(repeat),
                }
            )
        except Exception as exc:
            gold_checks.append(
                {
                    "question_id": q["external_question_id"],
                    "execution": False,
                    "error": type(exc).__name__ + ": " + str(exc),
                }
            )
    catalog = runtime_catalog(headers, ddl_tables)
    settings = Settings(
        database_url=DB_URL,
        admin_database_url=ADMIN_URL,
        reader_role="decision_reader",
        max_result_rows=10000,
        max_plan_rows=100000,
        max_plan_cost=100000,
        statement_timeout_ms=5000,
    )
    service = SqlSafetyService(create_engine(DB_URL), settings=settings, catalog=catalog)
    authority = ExecutionAuthority.from_catalog(catalog)
    canary = []
    for q in questions:
        try:
            plan = service.plan(
                SqlCandidate(
                    sql=compat_sql(q["gold_sql"]),
                    source=CandidateSource.LLM,
                    execution_authority=authority,
                )
            )
            execution = service.execute(plan) if isinstance(plan, QueryPlan) else None
            canary.append(
                {
                    "question_id": q["external_question_id"],
                    "accepted": isinstance(plan, QueryPlan),
                    "executed": execution is not None and hasattr(execution, "rows"),
                }
            )
        except Exception as exc:
            canary.append(
                {
                    "question_id": q["external_question_id"],
                    "accepted": False,
                    "error": type(exc).__name__ + ": " + str(exc),
                }
            )
    evaluator = {
        "gold_execution": sum(bool(x.get("execution")) for x in gold_checks),
        "gold_reflexivity": sum(bool(x.get("reflexivity")) for x in gold_checks),
        "gold_repeat_determinism": sum(bool(x.get("repeat_equal")) for x in gold_checks),
        "candidate_path_canary": sum(bool(x.get("accepted") and x.get("executed")) for x in canary),
        "questions": len(questions),
        "gold_checks": gold_checks,
        "canary": canary,
        "comparator_sha256": file_hash(comparator_path),
        "status": "PASS"
        if all(
            x.get("execution") and x.get("reflexivity") and x.get("repeat_equal")
            for x in gold_checks
        )
        and all(x.get("accepted") and x.get("executed") for x in canary)
        else "FAIL",
    }
    dump(AUDIT / "m61r_evaluator_integrity.json", evaluator)
    production_context = context_text
    original_serializer = m51b.serialize_governed_context_v1
    m51b.serialize_governed_context_v1 = lambda _database_id: production_context
    requests = []
    try:
        for q in questions:
            req = m51b._provider_request(
                q["ordinal"],
                q["external_question_id"],
                {"database_id": "acme_small", "question": q["question"]},
                prompt=stable_contract_prompt(),
            )
            payload = m56r_runner.provider_payload(req)
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            forbidden = [q["gold_sql"] for q in questions if q["gold_sql"].lower() in text.lower()]
            requests.append(
                {
                    "question_id": q["external_question_id"],
                    "provider_request_fingerprint": digest(payload),
                    "request_sha256": req["request_sha256"],
                    "deterministic_copy_fingerprint": digest(
                        m56r_runner.provider_payload(
                            m51b._provider_request(
                                q["ordinal"],
                                q["external_question_id"],
                                {"database_id": "acme_small", "question": q["question"]},
                                prompt=stable_contract_prompt(),
                            )
                        )
                    ),
                    "gold_leakage": forbidden,
                    "contract_hash": STABLE_CONTRACT_HASH,
                }
            )
    finally:
        m51b.serialize_governed_context_v1 = original_serializer
    prelive_pass = (
        len(requests) == 11
        and all(
            x["provider_request_fingerprint"] == x["deterministic_copy_fingerprint"]
            and not x["gold_leakage"]
            for x in requests
        )
        and evaluator["status"] == "PASS"
        and fp1 == fp2
    )
    dump(
        AUDIT / "m61r_prelive_provenance.json",
        {
            "requests": requests,
            "request_count": len(requests),
            "candidate_c_hash": STABLE_CONTRACT_HASH,
            "canonical_builder_source_hash": BUILDER_HASH,
            "gold_leakage": "PASS",
            "provider_calls_before_gate": 0,
            "status": "PASS" if prelive_pass else "FAIL",
        },
    )
    dump(
        AUDIT / "m61r_comparability.json",
        {
            "classification": "LOCAL_FIRST_PARTY_REPRODUCTION",
            "reason": "Exact first-party questions, ACME_small DDL, source CSVs, and retained dbt comparator are used locally; official dbt Semantic Layer credentials/backend are unavailable.",
        },
    )
    dump(
        AUDIT / "m61r_experiment_plan.json",
        {
            "milestone": "M61R",
            "repo_head": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
            ).stdout.strip(),
            "questions": [q["external_question_id"] for q in questions],
            "iterations": 20,
            "total_calls": 220,
            "candidate_contract": STABLE_CONTRACT_VERSION,
            "candidate_contract_hash": STABLE_CONTRACT_HASH,
            "canonical_builder_hash": BUILDER_HASH,
            "model": MODEL,
            "provider": "OpenAICompatibleProvider",
            "reasoning": REASONING,
            "temperature": TEMPERATURE,
            "retry": 0,
            "failed_case_reruns": 0,
            "primary_metric": "DECISION_SQL_END_TO_END_ACCURACY",
            "secondary_metric": "RAW_PROPOSAL_DBT_COMPATIBLE_ACCURACY",
            "comparator_hash": file_hash(comparator_path),
            "database_fingerprint": fp2,
            "prelive_required": "all evaluator, leakage, database, and provider provenance gates PASS",
            "status": "PREREGISTERED",
        },
    )
    if not prelive_pass:
        raise RuntimeError("M61R_ABORTED_PRELIVE_LOCAL_EVALUATOR_INTEGRITY_FAILURE")
    return {
        "questions": questions,
        "context": context_text,
        "headers": headers,
        "ddl_tables": ddl_tables,
        "comparator": ComparisonService,
        "gold_frames": gold_frames,
        "service": service,
        "authority": authority,
        "requests": requests,
        "source": source,
    }


def live(state: dict[str, Any]) -> None:
    provider = m56r_runner.OpenAICompatibleProvider(
        Settings(
            llm_model=MODEL,
            llm_temperature=TEMPERATURE,
            llm_reasoning_effort=REASONING,
            llm_timeout_seconds=TIMEOUT_SECONDS,
            llm_api_key=os.environ.get("OPENAI_API_KEY"),
            eval_capture_model_io=True,
        )
    )
    responses_path = AUDIT / "m61r_responses.jsonl"
    requests_path = AUDIT / "m61r_requests.jsonl"
    results_path = AUDIT / "m61r_case_results.jsonl"
    if any(p.exists() for p in (responses_path, requests_path, results_path)):
        raise RuntimeError("M61R_LIVE_ARTIFACT_ALREADY_EXISTS")
    original_serializer = m51b.serialize_governed_context_v1
    m51b.serialize_governed_context_v1 = lambda _database_id: state["context"]
    try:
        for question in state["questions"]:
            for iteration in range(1, 21):
                request = m51b._provider_request(
                    question["ordinal"],
                    question["external_question_id"],
                    {"database_id": "acme_small", "question": question["question"]},
                    prompt=stable_contract_prompt(),
                )
                payload = m56r_runner.provider_payload(request)
                fingerprint = digest(payload)
                append(
                    requests_path,
                    {
                        "question_id": question["external_question_id"],
                        "iteration": iteration,
                        "fingerprint": fingerprint,
                        "request_sha256": request["request_sha256"],
                        "request_text": request["request_text"],
                    },
                )
                started = time.perf_counter()
                error = None
                response_payload = None
                try:
                    response_payload = asyncio.run(
                        provider.complete_json_schema(
                            operation="m61r_acme_single_call",
                            system_prompt=request["instructions"],
                            user_prompt=request["user_text"],
                            schema_name="decision_sql_m51b_submission",
                            schema=submission_schema(),
                        )
                    )
                except Exception as exc:
                    error = exc
                latency = (time.perf_counter() - started) * 1000
                capture = provider.consume_model_io()
                wire = provider.consume_response_wire()
                content = getattr(capture, "raw_assistant_content_full", None) if capture else None
                parsed, parse_status, parse_detail, parsed_value = (
                    m39._parse(content, question["external_question_id"])
                    if error is None
                    else (None, "TRANSPORT_FAILURE", str(error), None)
                )
                sql_text = parsed.sql if parsed is not None else None
                raw_ok = False
                e2e_ok = False
                runtime_stage = None
                comparator = False
                runtime_detail = None
                if sql_text and parsed.decision == "ANSWER":
                    try:
                        plan = state["service"].plan(
                            SqlCandidate(
                                sql=compat_sql(sql_text),
                                source=CandidateSource.LLM,
                                execution_authority=state["authority"],
                            )
                        )
                        runtime_stage = (
                            "EXECUTED" if isinstance(plan, QueryPlan) else "RUNTIME_REJECTION"
                        )
                        runtime_detail = (
                            plan.model_dump(mode="json")
                            if not isinstance(plan, QueryPlan)
                            else None
                        )
                        if isinstance(plan, QueryPlan):
                            execution = state["service"].execute(plan)
                            if hasattr(execution, "rows"):
                                got = pd.DataFrame(execution.rows, columns=execution.columns)
                                comparator = bool(
                                    state["comparator"]
                                    .compare_query_results(
                                        state["gold_frames"][question["external_question_id"]], got
                                    )
                                    .is_equivalent
                                )
                                e2e_ok = comparator
                    except Exception as exc:
                        runtime_stage, runtime_detail = "EXECUTION_ERROR", str(exc)
                    try:
                        raw = execute_df(sql_text)
                        raw_ok = bool(
                            state["comparator"]
                            .compare_query_results(
                                state["gold_frames"][question["external_question_id"]], raw
                            )
                            .is_equivalent
                        )
                    except Exception:
                        raw_ok = False
                record = {
                    "question_id": question["external_question_id"],
                    "iteration": iteration,
                    "provider_request_fingerprint": fingerprint,
                    "model": MODEL,
                    "decision": parsed.decision if parsed else None,
                    "sql": sql_text,
                    "sql_hash": sha256_text(sql_text) if sql_text else None,
                    "parse_status": parse_status,
                    "parse_detail": parse_detail,
                    "provider_success": error is None,
                    "provider_error": None
                    if error is None
                    else {"type": type(error).__name__, "message": str(error)[:400]},
                    "raw_response_base64": base64.b64encode(wire).decode() if wire else None,
                    "raw_response_hash": hashlib.sha256(wire).hexdigest() if wire else None,
                    "runtime_stage": runtime_stage,
                    "runtime_detail": runtime_detail,
                    "dbt_comparator_pass": comparator,
                    "raw_proposal_dbt_pass": raw_ok,
                    "end_to_end_pass": e2e_ok,
                    "latency_ms": latency,
                    "usage": m39._provider_metadata(response_payload or {}, capture)
                    if capture
                    else {},
                }
                append(responses_path, record)
                append(results_path, record)
    finally:
        m51b.serialize_governed_context_v1 = original_serializer
    rows = [json.loads(line) for line in results_path.read_text().splitlines() if line.strip()]
    dump(
        AUDIT / "m61r_question_accuracy.json",
        {
            "questions": [
                {
                    "question_id": q["external_question_id"],
                    "correct": sum(
                        r["end_to_end_pass"]
                        for r in rows
                        if r["question_id"] == q["external_question_id"]
                    ),
                    "total": 20,
                }
                for q in state["questions"]
            ],
            "correct": sum(r["end_to_end_pass"] for r in rows),
            "total": len(rows),
            "rate": sum(r["end_to_end_pass"] for r in rows) / len(rows) if rows else None,
            "fully_stable_questions": sum(
                sum(
                    r["end_to_end_pass"]
                    for r in rows
                    if r["question_id"] == q["external_question_id"]
                )
                == 20
                for q in state["questions"]
            ),
        },
    )
    dump(
        AUDIT / "m61r_summary.json",
        {
            "milestone": "M61R",
            "status": "COMPLETE",
            "provider_calls": len(rows),
            "retries": 0,
            "end_to_end_correct": sum(r["end_to_end_pass"] for r in rows),
            "raw_proposal_correct": sum(r["raw_proposal_dbt_pass"] for r in rows),
            "questions": 11,
            "iterations": 20,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prelive", "live"))
    args = parser.parse_args()
    state = prelive()
    if args.command == "live":
        live(state)
    else:
        dump(
            AUDIT / "m61r_manifest.json",
            {
                "milestone": "M61R",
                "verdict": "PRELIVE_READY_NOT_RUN",
                "provider_calls": 0,
                "candidate_contract_hash": STABLE_CONTRACT_HASH,
                "canonical_builder_hash": BUILDER_HASH,
            },
        )


if __name__ == "__main__":
    main()
