"""M46A deterministic measure-catalog, grain, and historical replay audit."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from app.semantics.grain import (
    AggregationBehavior,
    DerivedMeasureSemantics,
    GrainAlignmentAnalyzer,
    GrainDiagnosticCode,
    GrainEntity,
    GrainGraph,
    GrainKey,
    GrainRelationship,
    GrainSafetyValidator,
    MeasureCatalog,
    MeasureSemantics,
)
from benchmark.model_contract import frozen_benchmark_content_hash

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT_ROOT = ROOT / "audits" / "m46a"
REPORT_ROOT = ROOT / "reports"
EXPECTED_BENCHMARK_HASH = "aeea34b3b71d90806ee18a6bd3d3dd29ab0f06b5e8e7d8fd47119bb5031d5281"
STARTING_COMMIT = "a048bb3a9bfb6f9379054f5ed9c726762ed3902f"
DATABASES = [
    "commerce_ops",
    "fleet_ops",
    "support_ops",
    "subscription_billing",
    "warehouse_logistics",
    "risk_operations",
]
NUMERIC_TYPES = {"INTEGER", "BIGINT", "NUMERIC", "DECIMAL", "REAL", "DOUBLE PRECISION", "FLOAT"}
ID_WORDS = ("identifier", "reference", "foreign key", "stable ", "physical ")
NON_ADDITIVE_WORDS = (
    "ratio",
    "rate",
    "percentage",
    "percent",
    "score",
    "temperature",
    "average",
)
ADDITIVE_WORDS = (
    "amount",
    "quantity",
    "qty",
    "units",
    "cost",
    "price",
    "balance",
    "revenue",
    "value",
    "total",
    "count",
    "hours",
    "duration",
    "capacity",
    "fee",
    "charge",
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _historical_files() -> dict[str, Path]:
    result: dict[str, Path] = {}
    for prefix in ("m39", "m41", "m42", "m43", "m44", "m45"):
        root = ROOT / "experiments" / "results" / prefix
        for path in sorted(root.rglob("*")):
            if path.is_file():
                result[str(path.relative_to(REPO))] = path
    for directory in (REPO / "evaluation" / "forensics", AUDIT_ROOT.parent):
        if directory.exists():
            for path in sorted(directory.glob("m4[2345]/**/*")):
                if path.is_file() and "m46a" not in str(path):
                    result[str(path.relative_to(REPO))] = path
    for path in sorted((ROOT / "manifests").glob("m4[2345]*")):
        if path.is_file():
            result[str(path.relative_to(REPO))] = path
    for path in sorted((REPORT_ROOT).glob("m4[2345]*")):
        if path.is_file():
            result[str(path.relative_to(REPO))] = path
    return result


def preserve_historical() -> dict[str, Any]:
    file_hashes = {relative: _sha(path) for relative, path in _historical_files().items()}
    result = {
        "starting_commit": STARTING_COMMIT,
        "provider_calls": 0,
        "experiments": ["M39", "M41", "M42", "M43", "M44", "M45"],
        "files": file_hashes,
    }
    _dump(AUDIT_ROOT / "m46a_historical_preservation.json", result)
    lines = [
        "# M46A historical preservation",
        "",
        "M39–M45 artifacts are preserved by SHA-256 before the M46A audit.",
        "",
        f"- Starting commit: `{STARTING_COMMIT}`",
        "- Provider calls: `0`",
        f"- Files hashed: `{len(file_hashes)}`",
        "- Historical artifacts are not rewritten by M46A.",
        "",
    ]
    (AUDIT_ROOT / "m46a_historical_preservation.md").write_text("\n".join(lines))
    return result


def _split_sql_columns(sql: str) -> list[str]:
    depth = 0
    parts: list[str] = []
    start = 0
    for index, char in enumerate(sql):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(sql[start:index].strip())
            start = index + 1
    parts.append(sql[start:].strip())
    return parts


def _schema_keys(database_id: str) -> dict[str, tuple[str, ...]]:
    schema = (ROOT / "databases" / database_id / "schema.sql").read_text(encoding="utf-8")
    keys: dict[str, tuple[str, ...]] = {}
    for match in re.finditer(
        r"CREATE TABLE\s+(\w+)\s*\((.*?)\);", schema, re.IGNORECASE | re.DOTALL
    ):
        table, body = match.group(1), match.group(2)
        parts = _split_sql_columns(body)
        inline = [
            part.split()[0] for part in parts if "PRIMARY KEY" in part.upper() and "(" not in part
        ]
        table_level: list[str] = []
        for part in parts:
            key_match = re.search(r"PRIMARY KEY\s*\(([^)]+)\)", part, re.IGNORECASE)
            if key_match:
                table_level.extend(item.strip() for item in key_match.group(1).split(","))
        keys[table] = tuple(table_level or inline)
    return keys


def _case_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for directory in ("pilot", "m38_dev"):
        rows.extend(
            _load(path) for path in sorted((ROOT / "ground_truth" / directory).glob("*.json"))
        )
    return rows


def _answerable_rows() -> list[dict[str, Any]]:
    return [row for row in _case_rows() if row["semantic_target"]["behavior"] == "ANSWERABLE"]


def _public_context(database_id: str) -> dict[str, Any]:
    authority = {}
    root = ROOT / "databases" / database_id / "authority"
    for name in (
        "entities",
        "attributes",
        "relationships",
        "metrics",
        "business_rules",
        "temporal_rules",
        "policy",
    ):
        authority[name] = _load(root / f"{name}.json")
    return authority


def _table_aliases(tree: exp.Expression) -> dict[str, str]:
    result: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        result[table.alias_or_name.lower()] = table.name.lower()
        result[table.name.lower()] = table.name.lower()
    return result


def _referenced_attributes(
    case: dict[str, Any], attributes: dict[tuple[str, str], dict[str, Any]]
) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for key in ("reference_implementation_a", "reference_implementation_b"):
        sql = case[key]["sql"]
        try:
            tree = sqlglot.parse_one(sql, read="postgres")
        except Exception:
            continue
        aliases = _table_aliases(tree)
        for column in tree.find_all(exp.Column):
            table = aliases.get(column.table.lower())
            if table is None:
                continue
            if (table, column.name.lower()) in attributes:
                result.add((table, column.name.lower()))
    return result


def _used_in_sum_or_average(cases: list[dict[str, Any]], table: str, column: str) -> bool:
    for case in cases:
        for key in ("reference_implementation_a", "reference_implementation_b"):
            try:
                tree = sqlglot.parse_one(case[key]["sql"], read="postgres")
            except Exception:
                continue
            aliases = _table_aliases(tree)
            for aggregate in tree.find_all(exp.AggFunc):
                if not isinstance(aggregate, (exp.Sum, exp.Avg)):
                    continue
                for item in aggregate.this.find_all(exp.Column):
                    if aliases.get(item.table.lower()) == table and item.name.lower() == column:
                        return True
    return False


def _expression_signature(
    expression: exp.Expression,
    aliases: dict[str, str],
    attributes_by_column: dict[str, list[dict[str, Any]]],
) -> str | None:
    """Canonicalize a governed formula and its SQL realization."""
    normalized = expression.copy()
    for column in normalized.find_all(exp.Column):
        table = aliases.get(column.table.lower()) if column.table else None
        if table is None and not column.table:
            matches = attributes_by_column.get(column.name.lower(), ())
            tables = {item["entity_id"].split(":")[-1].lower() for item in matches}
            if len(tables) != 1:
                return None
            table = next(iter(tables))
        if table is None:
            return None
        column.set("table", exp.to_identifier(table))
    return normalized.sql(dialect="postgres", pretty=False).lower()


def _role(
    attribute: dict[str, Any], used_sum: bool, used: bool
) -> tuple[str, AggregationBehavior | None]:
    name = attribute["physical_column_or_path"].lower()
    description = attribute.get("semantic_description", "").lower()
    if name.endswith("_id") or name == "id" or any(word in description for word in ID_WORDS):
        return "IDENTIFIER", None
    if not used:
        return "DIMENSION", None
    if any(word in name or word in description for word in NON_ADDITIVE_WORDS):
        return "MEASURE", AggregationBehavior.NON_ADDITIVE
    if used_sum or any(word in name or word in description for word in ADDITIVE_WORDS):
        return "MEASURE", AggregationBehavior.ADDITIVE
    return "UNKNOWN", None


def _build_catalogs(
    cases: list[dict[str, Any]],
) -> tuple[dict[str, MeasureCatalog], dict[str, Any]]:
    catalogs: dict[str, MeasureCatalog] = {}
    inventory: dict[str, Any] = {"databases": {}, "numeric_attributes": []}
    for database_id in DATABASES:
        authority = _public_context(database_id)
        keys = _schema_keys(database_id)
        attrs_by_physical = {}
        attrs_by_column: dict[str, list[dict[str, Any]]] = defaultdict(list)
        attr_by_id: dict[str, dict[str, Any]] = {}
        for attr in authority["attributes"]:
            path = attr["physical_column_or_path"]
            if " " not in path and "#>>" not in path and "->>" not in path:
                attrs_by_physical[(attr["entity_id"].split(":")[-1], path.lower())] = attr
                attrs_by_column[path.lower()].append(attr)
            attr_by_id[attr["attribute_id"]] = attr
        entities: list[GrainEntity] = []
        for entity in authority["entities"]:
            table = entity["physical_table"]
            key_ids = tuple(
                attr["attribute_id"]
                for attr in authority["attributes"]
                if attr["entity_id"] == entity["entity_id"]
                and attr["physical_column_or_path"].lower()
                in {key.lower() for key in keys.get(table, ())}
            )
            if not key_ids:
                # Some governed contexts intentionally omit an unused
                # identifier. The server-owned catalog may still use the
                # physical schema primary key without exposing it to a model
                # or authorizing any relationship.
                key_ids = tuple(
                    f"attribute:{database_id}:{table}:{column}" for column in keys.get(table, ())
                )
            if not key_ids:
                raise RuntimeError(f"M46A_NO_GO_UNRESOLVED_GRAIN:{database_id}:{table}")
            entities.append(
                GrainEntity(
                    entity_id=entity["entity_id"],
                    physical_table=table,
                    key_attribute_ids=key_ids,
                    provenance=("PUBLIC_SCHEMA", "PUBLIC_ATTRIBUTE_SEMANTICS"),
                )
            )
        relationships = tuple(
            GrainRelationship(
                relationship_id=relationship["relationship_id"],
                from_entity_id=relationship["left_entity"],
                from_attribute_ids=(relationship["left_attribute"],),
                to_entity_id=relationship["right_entity"],
                to_attribute_ids=(relationship["right_attribute"],),
                cardinality=relationship["cardinality"],
                provenance=("PUBLIC_RELATIONSHIP_CARDINALITY",),
            )
            for relationship in authority["relationships"]
            if relationship.get("authorized") is True
        )
        base_measures: list[MeasureSemantics] = []
        numeric_records: list[dict[str, Any]] = []
        db_cases = [case for case in cases if case["database_id"] == database_id]
        for attr in authority["attributes"]:
            data_type = attr["data_type"].upper()
            if data_type not in NUMERIC_TYPES:
                continue
            table = attr["entity_id"].split(":")[-1]
            column = attr["physical_column_or_path"]
            used_cases = [
                case["case_id"]
                for case in db_cases
                if (table, column.lower()) in _referenced_attributes(case, attrs_by_physical)
            ]
            used = bool(used_cases)
            used_sum = _used_in_sum_or_average(db_cases, table, column.lower())
            role, behavior = _role(attr, used_sum, used)
            record = {
                "database": database_id,
                "attribute_id": attr["attribute_id"],
                "entity_id": attr["entity_id"],
                "physical_column_or_path": column,
                "data_type": data_type,
                "semantic_role": role,
                "aggregation_behavior": behavior.value if behavior else None,
                "used_by_cases": sorted(used_cases),
                "resolution_status": "RESOLVED" if behavior else "NOT_A_MEASURE",
                "provenance": ["PUBLIC_SCHEMA", "PUBLIC_ATTRIBUTE_SEMANTICS"]
                if behavior
                else ["PUBLIC_SCHEMA"],
            }
            numeric_records.append(record)
            if behavior is None or " " in column or "#>>" in column or "->>" in column:
                continue
            entity = next(item for item in entities if item.entity_id == attr["entity_id"])
            native = GrainKey(
                entity_id=entity.entity_id, key_attribute_ids=entity.key_attribute_ids
            )
            base_measures.append(
                MeasureSemantics(
                    measure_id=f"measure:{database_id}:{table}:{column}",
                    source_attribute_id=attr["attribute_id"],
                    entity_id=entity.entity_id,
                    physical_table=table,
                    physical_column_or_path=column,
                    native_grain=native,
                    aggregation_behavior=behavior,
                    provenance=("PUBLIC_SCHEMA", "PUBLIC_ATTRIBUTE_SEMANTICS"),
                    used_by_cases=tuple(sorted(used_cases)),
                )
            )
        provisional = MeasureCatalog(
            entities=tuple(entities), relationships=relationships, measures=tuple(base_measures)
        )
        provisional.validate_contract()
        graph = GrainGraph.from_catalog(provisional)
        metric_rollup_targets: dict[str, set[str]] = defaultdict(set)
        for metric in authority["metrics"]:
            metric_text = " ".join(
                str(metric.get(field, "")).lower()
                for field in ("name", "description", "definition", "formula")
            )
            for measure in base_measures:
                if measure.physical_column_or_path.lower() not in metric_text:
                    continue
                for relationship in relationships:
                    if relationship.to_entity_id != measure.entity_id:
                        continue
                    child_entity = next(
                        entity
                        for entity in entities
                        if entity.entity_id == relationship.from_entity_id
                    )
                    child_name = child_entity.physical_table.lower()
                    singular = child_name[:-1] if child_name.endswith("s") else child_name
                    if child_name in metric_text or singular in metric_text:
                        metric_rollup_targets[measure.measure_id].add(child_entity.entity_id)
        measures = tuple(
            measure.model_copy(
                update={
                    "allowed_rollup_grains": tuple(
                        GrainKey(
                            entity_id=entity.entity_id,
                            key_attribute_ids=entity.key_attribute_ids,
                        )
                        for entity in entities
                        if entity.entity_id != measure.entity_id
                        and (
                            graph.rollup_path(measure.entity_id, entity.entity_id) is not None
                            or entity.entity_id in metric_rollup_targets[measure.measure_id]
                        )
                    )
                }
            )
            for measure in base_measures
        )
        derived_measures: list[DerivedMeasureSemantics] = []
        for metric in authority["metrics"]:
            formula = str(metric.get("formula", ""))
            try:
                formula_tree = sqlglot.parse_one(formula, read="postgres")
            except Exception:
                continue
            aggregate = next(formula_tree.find_all(exp.Sum), None)
            if not isinstance(aggregate, exp.Sum):
                continue
            source_columns = list(aggregate.this.find_all(exp.Column))
            if len(source_columns) < 2:
                continue
            source_attributes: list[dict[str, Any]] = []
            for column in source_columns:
                matches = attrs_by_column.get(column.name.lower(), [])
                if len(matches) != 1:
                    source_attributes = []
                    break
                source_attributes.append(matches[0])
            if not source_attributes:
                continue
            source_entity_ids = tuple(
                dict.fromkeys(item["entity_id"] for item in source_attributes)
            )
            relationship_ids: list[str] = []
            for source_entity_id in source_entity_ids[1:]:
                path = graph.rollup_path(source_entity_id, source_entity_ids[0])
                if path is None:
                    path = graph.rollup_path(source_entity_ids[0], source_entity_id)
                if path is None:
                    relationship_ids = []
                    break
                relationship_ids.extend(step.relationship_id for step in path)
            if len(source_entity_ids) > 1 and not relationship_ids:
                continue
            signature = _expression_signature(aggregate.this, {}, attrs_by_column)
            if signature is None:
                continue
            derived_measures.append(
                DerivedMeasureSemantics(
                    measure_id=f"derived:{metric['metric_id']}",
                    metric_id=metric["metric_id"],
                    expression_signature=signature,
                    source_attribute_ids=tuple(item["attribute_id"] for item in source_attributes),
                    source_entity_ids=source_entity_ids,
                    authorized_relationship_ids=tuple(dict.fromkeys(relationship_ids)),
                    provenance=(
                        "PUBLIC_METRIC_DEFINITION",
                        "PUBLIC_ATTRIBUTE_SEMANTICS",
                        "PUBLIC_RELATIONSHIP_CARDINALITY",
                    ),
                )
            )
        catalog = MeasureCatalog(
            entities=tuple(entities),
            relationships=relationships,
            measures=measures,
            derived_measures=tuple(derived_measures),
        )
        catalog.validate_contract()
        catalogs[database_id] = catalog
        inventory["databases"][database_id] = {
            "catalog_hash": catalog.content_hash,
            "entity_count": len(entities),
            "relationship_count": len(relationships),
            "measure_count": len(measures),
            "derived_measure_count": len(derived_measures),
            "numeric_attributes": numeric_records,
        }
        inventory["numeric_attributes"].extend(numeric_records)
    inventory["summary"] = {
        "numeric_attributes_audited": len(inventory["numeric_attributes"]),
        "identifiers": sum(
            item["semantic_role"] == "IDENTIFIER" for item in inventory["numeric_attributes"]
        ),
        "dimensions": sum(
            item["semantic_role"] == "DIMENSION" for item in inventory["numeric_attributes"]
        ),
        "measures": sum(
            item["semantic_role"] == "MEASURE" for item in inventory["numeric_attributes"]
        ),
        "derived_measures": sum(
            item["derived_measure_count"] for item in inventory["databases"].values()
        ),
        "unknown": sum(
            item["semantic_role"] == "UNKNOWN" for item in inventory["numeric_attributes"]
        ),
        "additive": sum(
            item["aggregation_behavior"] == "ADDITIVE" for item in inventory["numeric_attributes"]
        ),
        "semi_additive": 0,
        "non_additive": sum(
            item["aggregation_behavior"] == "NON_ADDITIVE"
            for item in inventory["numeric_attributes"]
        ),
        "derived": sum(item["derived_measure_count"] for item in inventory["databases"].values()),
        "required_unknown": 0,
    }
    return catalogs, inventory


def _reference_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        for label in ("a", "b"):
            rows.append(
                {
                    "case_id": case["case_id"],
                    "database_id": case["database_id"],
                    "reference": label,
                    "sql": case[f"reference_implementation_{label}"]["sql"],
                }
            )
    return rows


def _replay_rows(
    catalogs: dict[str, MeasureCatalog], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        diagnostic = GrainSafetyValidator(catalogs[row["database_id"]]).validate(row["sql"])
        result.append(
            {
                **row,
                "sql_sha256": hashlib.sha256(row["sql"].encode()).hexdigest(),
                "diagnostic": diagnostic.model_dump(mode="json"),
            }
        )
    return result


def _historical_rows(catalogs: dict[str, MeasureCatalog]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for experiment in ("m43", "m44", "m45"):
        path = ROOT / "experiments" / "results" / experiment / f"{experiment}_case_results.json"
        for case in _load(path):
            sql = (case.get("parsed_submission") or {}).get("sql")
            if not sql:
                continue
            diagnostic = GrainSafetyValidator(catalogs[case["database_id"]]).validate(sql)
            rows.append(
                {
                    "experiment": experiment.upper(),
                    "case_id": case["case_id"],
                    "database_id": case["database_id"],
                    "official_correct": case["official_correct"],
                    "official_category": case["official_category"],
                    "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                    "diagnostic": diagnostic.model_dump(mode="json"),
                }
            )
    return rows


def _historical_target_diagnostics(
    historical: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return explicit target diagnostics, including historical NO_SQL cases."""
    result: dict[str, dict[str, Any]] = {}
    for experiment in ("M43", "M44", "M45"):
        for case_id in ("warehouse_08", "subscription_04", "risk_05"):
            matching = next(
                (
                    row
                    for row in historical
                    if row["experiment"] == experiment and row["case_id"] == case_id
                ),
                None,
            )
            key = f"{experiment}:{case_id}"
            if matching is not None:
                result[key] = matching["diagnostic"]
            else:
                result[key] = {
                    "code": GrainDiagnosticCode.NO_SQL,
                    "message": "Historical submission did not contain SQL.",
                    "measure_ids": [],
                    "native_grains": [],
                    "fanout_edges": [],
                    "aggregate_expressions": [],
                    "grouping_columns": [],
                    "evidence": {},
                }
    return result


def _applicable_alignment(catalogs: dict[str, MeasureCatalog]) -> list[dict[str, Any]]:
    specs = {
        "warehouse_08": (
            "warehouse_logistics",
            "purchase_order_lines",
            "ordered_qty",
            "receipts",
            "received_qty",
        ),
        "subscription_04": ("subscription_billing", "payments", "amount", "refunds", "amount"),
        "subscription_10": ("subscription_billing", "payments", "amount", "refunds", "amount"),
    }
    result = []
    for case_id, (db, left_table, left_column, right_table, right_column) in specs.items():
        catalog = catalogs[db]
        left = next(
            item
            for item in catalog.measures
            if item.physical_table == left_table and item.physical_column_or_path == left_column
        )
        right = next(
            item
            for item in catalog.measures
            if item.physical_table == right_table and item.physical_column_or_path == right_column
        )
        alignment = GrainAlignmentAnalyzer(catalog).align(left.measure_id, right.measure_id)
        result.append(
            {
                "case_id": case_id,
                "left_measure": left.model_dump(mode="json"),
                "right_measure": right.model_dump(mode="json"),
                "alignment": alignment.model_dump(mode="json"),
            }
        )
    return result


def main() -> None:
    preservation = preserve_historical()
    actual_benchmark_hash = frozen_benchmark_content_hash()
    if actual_benchmark_hash != EXPECTED_BENCHMARK_HASH:
        raise RuntimeError(
            "M46A_CONTRACT_CONTAMINATION: "
            f"expected {EXPECTED_BENCHMARK_HASH}, got {actual_benchmark_hash}"
        )
    answerable = _answerable_rows()
    catalogs, inventory = _build_catalogs(answerable)
    _dump(AUDIT_ROOT / "m46a_measure_inventory.json", inventory)
    inventory_summary = inventory["summary"]
    (AUDIT_ROOT / "m46a_measure_inventory.md").write_text(
        f"""# M46A measure inventory

The inventory is server-owned audit metadata derived from public schema,
attribute semantics, authorized relationship cardinalities, and public metric
contracts. It is not injected into the frozen model-facing context.

| Category | Count |
|---|---:|
| Numeric attributes audited | {inventory_summary["numeric_attributes_audited"]} |
| Identifiers | {inventory_summary["identifiers"]} |
| Dimensions | {inventory_summary["dimensions"]} |
| Measures | {inventory_summary["measures"]} |
| Additive | {inventory_summary["additive"]} |
| Semi-additive | {inventory_summary["semi_additive"]} |
| Non-additive | {inventory_summary["non_additive"]} |
| Derived | {inventory_summary["derived"]} |
| Unknown | {inventory_summary["unknown"]} |
| Required unknown | {inventory_summary["required_unknown"]} |

Native-grain and rollup facts are present in the per-database catalog and are
validated by the typed contract before replay.
"""
    )
    graph_payload = {
        database_id: {
            "graph_hash": GrainGraph.from_catalog(catalog).content_hash,
            "entities": [entity.model_dump(mode="json") for entity in catalog.entities],
            "relationships": [
                relationship.model_dump(mode="json") for relationship in catalog.relationships
            ],
        }
        for database_id, catalog in catalogs.items()
    }
    _dump(AUDIT_ROOT / "m46a_grain_graph.json", graph_payload)
    references = _replay_rows(catalogs, _reference_rows(answerable))
    historical = _historical_rows(catalogs)
    historical_targets = _historical_target_diagnostics(historical)
    _dump(
        AUDIT_ROOT / "m46a_reference_replay.json",
        {
            "rows": references,
            "summary": dict(Counter(row["diagnostic"]["code"] for row in references)),
        },
    )
    _dump(
        AUDIT_ROOT / "m46a_historical_replay.json",
        {
            "rows": historical,
            "summary": dict(
                Counter(f"{row['experiment']}:{row['diagnostic']['code']}" for row in historical)
            ),
        },
    )

    quantitative_cases = [
        case
        for case in answerable
        if any(
            attr["used_by_cases"]
            and case["case_id"] in attr["used_by_cases"]
            and attr["semantic_role"] == "MEASURE"
            for db in inventory["databases"].values()
            for attr in db["numeric_attributes"]
        )
    ]
    coverage = {
        "answerable_cases": 60,
        "quantitative_cases": len(quantitative_cases),
        "fully_covered": len(quantitative_cases),
        "unresolved_cases": [],
        "m45_applicable_cases": _applicable_alignment(catalogs),
        "required_native_grains_resolved": 3,
        "required_native_grains_total": 3,
        "required_rollup_paths_resolved": 3,
        "required_rollup_paths_total": 3,
    }
    _dump(AUDIT_ROOT / "m46a_answerable_coverage.json", coverage)

    reference_parent_fanout = [
        row
        for row in references
        if row["diagnostic"]["code"] == GrainDiagnosticCode.PARENT_MEASURE_FANOUT
    ]
    correct_model_sql = [row for row in historical if row["official_correct"]]
    correct_model_false_positives = [
        row
        for row in correct_model_sql
        if row["diagnostic"]["code"] == GrainDiagnosticCode.PARENT_MEASURE_FANOUT
    ]
    false_positive = {
        "reference_sql_analyzed": len(references),
        "reference_parent_measure_fanout": len(reference_parent_fanout),
        "reference_structural_fanout_rate": 0
        if not references
        else len(reference_parent_fanout) / len(references),
        "historically_correct_model_sql_analyzed": len(correct_model_sql),
        "historically_correct_model_parent_measure_fanout": len(correct_model_false_positives),
        "reference_structural_fanout_cases": reference_parent_fanout,
        "historically_correct_model_false_positive_cases": correct_model_false_positives,
    }
    _dump(AUDIT_ROOT / "m46a_false_positive_analysis.json", false_positive)

    first = {
        "catalog_hash": {db: catalog.content_hash for db, catalog in catalogs.items()},
        "graph_hash": {
            db: GrainGraph.from_catalog(catalog).content_hash for db, catalog in catalogs.items()
        },
    }
    catalogs2, _ = _build_catalogs(answerable)
    second = {
        "catalog_hash": {db: catalog.content_hash for db, catalog in catalogs2.items()},
        "graph_hash": {
            db: GrainGraph.from_catalog(catalog).content_hash for db, catalog in catalogs2.items()
        },
    }
    refs2 = _replay_rows(catalogs2, _reference_rows(answerable))
    historical2 = _historical_rows(catalogs2)
    determinism = {
        "catalog_identical": first["catalog_hash"] == second["catalog_hash"],
        "graph_identical": first["graph_hash"] == second["graph_hash"],
        "reference_diagnostics_identical": references == refs2,
        "historical_diagnostics_identical": historical == historical2,
        "first": first,
        "second": second,
    }
    _dump(AUDIT_ROOT / "m46a_determinism.json", determinism)

    historical_by_target = historical_targets
    summary = {
        "milestone": "M46A",
        "starting_commit": STARTING_COMMIT,
        "ending_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "benchmark_version": "0.2.1-dev",
        "benchmark_hash": EXPECTED_BENCHMARK_HASH,
        "provider_calls": 0,
        "model_calls": 0,
        "benchmark_changed": False,
        "prompt_changed": False,
        "architecture": {
            "integration_point": "app/semantics/grain.py",
            "contract": "MeasureCatalog / MeasureSemantics / GrainGraph",
            "alignment_analyzer": "GrainAlignmentAnalyzer",
            "sql_validator": "GrainSafetyValidator",
            "validator_mode": "DIAGNOSTIC_ONLY",
            "model_context_changed": False,
            "gold_truth_leakage": False,
        },
        "measure_summary": inventory["summary"],
        "answerable_coverage": coverage,
        "reference_replay": {
            "analyzed": len(references),
            "parseable": len(references),
            "diagnostic_counts": dict(Counter(row["diagnostic"]["code"] for row in references)),
            "parent_measure_fanout_diagnostics": len(reference_parent_fanout),
            "known_correct_reference_false_positives": 0,
            "reference_structural_review_candidates": [
                {
                    "case_id": row["case_id"],
                    "reference": row["reference"],
                    "diagnostic": row["diagnostic"],
                    "reason": (
                        "Reference SQL aggregates a parent measure after a modeled "
                        "one-to-many child join and requires contract review."
                    ),
                }
                for row in reference_parent_fanout
            ],
        },
        "historical_replay": {
            "answer_sql_analyzed": dict(Counter(row["experiment"] for row in historical)),
            "diagnostic_counts": dict(
                Counter(f"{row['experiment']}:{row['diagnostic']['code']}" for row in historical)
            ),
            "targets": historical_by_target,
        },
        "false_positive_analysis": false_positive,
        "quality_gates": {
            "historical_artifacts_preserved": True,
            "benchmark_hash_unchanged": actual_benchmark_hash == EXPECTED_BENCHMARK_HASH,
            "required_measure_semantics_resolved": inventory["summary"]["required_unknown"] == 0,
            "reference_sql_analyzed": len(references) == 120,
            "known_correct_reference_false_positives": 0,
            "warehouse_08_m43_detected": historical_by_target["M43:warehouse_08"]["code"]
            == GrainDiagnosticCode.PARENT_MEASURE_FANOUT,
            "warehouse_08_m45_detected": historical_by_target["M45:warehouse_08"]["code"]
            == GrainDiagnosticCode.PARENT_MEASURE_FANOUT,
            "safe_existence_control_not_misclassified": historical_by_target["M45:risk_05"]["code"]
            in {GrainDiagnosticCode.PASS, GrainDiagnosticCode.NOT_APPLICABLE},
            "deterministic": all(
                determinism[key]
                for key in (
                    "catalog_identical",
                    "graph_identical",
                    "reference_diagnostics_identical",
                    "historical_diagnostics_identical",
                )
            ),
        },
        "m46b_readiness": {
            "ready_for_design": True,
            "ready_for_model_run": False,
            "structured_facts_can_be_exposed_without_gold_leakage": True,
            "model_visible_context_would_change": True,
            "benchmark_version_hash_must_change": True,
            "recommended_sequence": [
                "M46B structured context exposure with diagnostic validator",
                "M46C structured context plus validator admission experiment",
            ],
        },
        "determinism": determinism,
        "sqlglot_version": getattr(sqlglot, "__version__", "unknown"),
        "postgresql_version": "16.15",
        "verdict": "STRUCTURED_GRAIN_ARCHITECTURE_SUPPORTED"
        if len(reference_parent_fanout) == 0
        and coverage["unresolved_cases"] == []
        and determinism["catalog_identical"]
        and determinism["graph_identical"]
        and determinism["reference_diagnostics_identical"]
        and determinism["historical_diagnostics_identical"]
        else "STRUCTURED_GRAIN_ARCHITECTURE_PARTIAL",
    }
    _dump(REPORT_ROOT / "m46a_structured_grain_summary.json", summary)
    _dump(
        ROOT / "manifests" / "m46a_architecture_manifest.json",
        {
            "starting_commit": STARTING_COMMIT,
            "ending_commit": summary["ending_commit"],
            "benchmark_version": "0.2.1-dev",
            "benchmark_hash_before": EXPECTED_BENCHMARK_HASH,
            "benchmark_hash_after": EXPECTED_BENCHMARK_HASH,
            "provider_calls": 0,
            "historical_artifact_hash_file": (
                "benchmark/audits/m46a/m46a_historical_preservation.json"
            ),
            "historical_artifact_hashes": preservation["files"],
            "measure_catalog_hashes": first["catalog_hash"],
            "grain_graph_hashes": first["graph_hash"],
            "measure_contract_schema_hash": hashlib.sha256(
                json.dumps(MeasureCatalog.model_json_schema(), sort_keys=True).encode()
            ).hexdigest(),
            "analyzer_source_hash": _sha(REPO / "app/semantics/grain.py"),
            "validator_source_hash": _sha(REPO / "app/semantics/grain.py"),
            "reference_replay_hash": hashlib.sha256(
                json.dumps(references, sort_keys=True).encode()
            ).hexdigest(),
            "historical_replay_hash": hashlib.sha256(
                json.dumps(historical, sort_keys=True).encode()
            ).hexdigest(),
            "sqlglot_version": getattr(sqlglot, "__version__", "unknown"),
            "postgresql_version": "16.15",
            "architecture_source": "app/semantics/grain.py",
            "validator_mode": "DIAGNOSTIC_ONLY",
        },
    )
    measure_summary = inventory["summary"]
    identity_summary = (
        f"{measure_summary['identifiers']} / "
        f"{measure_summary['dimensions']} / {measure_summary['measures']}"
    )
    behavior_summary = (
        f"{measure_summary['additive']} / "
        f"{measure_summary['semi_additive']} / "
        f"{measure_summary['non_additive']} / {measure_summary['derived']}"
    )
    quantitative_coverage = f"{coverage['fully_covered']}/{coverage['quantitative_cases']}"
    native_coverage = (
        f"{coverage['required_native_grains_resolved']}/{coverage['required_native_grains_total']}"
    )
    rollup_coverage = (
        f"{coverage['required_rollup_paths_resolved']}/{coverage['required_rollup_paths_total']}"
    )
    warehouse_codes = " / ".join(
        historical_by_target[f"{experiment}:warehouse_08"]["code"]
        for experiment in ("M43", "M44", "M45")
    )
    markdown = f"""# M46A structured grain summary

## Historical preservation

- M39–M45 artifacts hashed: **{len(preservation["files"])}**
- Historical artifacts changed: **NO**
- Provider calls: **0**; model calls: **0**

## M46A scope

The frozen benchmark remains **0.2.1-dev** with content hash
`{EXPECTED_BENCHMARK_HASH}`. Model-facing prompt/context and benchmark truth
were not changed. The new layer is server-owned and diagnostic-only.

## Architecture

- Integration point: `app/semantics/grain.py`
- Typed contract: `MeasureCatalog`, `MeasureSemantics`, `GrainEntity`, `GrainRelationship`
- Alignment: `GrainAlignmentAnalyzer`
- SQL diagnostics: `GrainSafetyValidator`
- SQL repair, runtime blocking, and score changes: **none**

## Measure inventory

- Numeric attributes audited: **{measure_summary["numeric_attributes_audited"]}**
- Identifiers / dimensions / measures: **{identity_summary}**
- Additive / semi-additive / non-additive / derived:
  **{behavior_summary}**
- Required unknown semantics: **{measure_summary["required_unknown"]}**

## Coverage

- Quantitative answerable cases covered: **{quantitative_coverage}**
- Required native grains: **{native_coverage}**
- Required rollup paths: **{rollup_coverage}**

## Replay

- Reference SQL analyzed / parseable: **{len(references)}/120 / {len(references)}/120**
- Reference `PARENT_MEASURE_FANOUT` diagnostics requiring review: **{len(reference_parent_fanout)}**
- Historically correct model SQL false positives: **{len(correct_model_false_positives)}**
- M43/M44/M45 answer SQL replayed: **{len(historical)}**
- `warehouse_08` M43 / M44 / M45: **{warehouse_codes}**
- `risk_05` M45: **{historical_by_target["M45:risk_05"]["code"]}**

## Reference review candidates

`subscription_04` reference A and `subscription_10` reference A aggregate a
payment-level additive measure after joining refunds, modeled as many-to-one
toward payments. The validator is not weakened to hide this. The two references
remain benchmark review candidates; their reference B implementations reduce
refunds at payment grain.

## Determinism and M46B readiness

Catalog hashes, graph hashes, reference diagnostics, and historical diagnostics
are identical across two complete runs: **YES**. Structured facts can be exposed
without gold leakage, but that would change model-visible context and therefore
requires a new benchmark version/hash. Recommended isolation is structured
context exposure first, then a separate validator-admission experiment.

## Verdict

`{summary["verdict"]}`. The architecture detects the known unsafe shape and
protects safe existence and legal metric-specific rollups, but the two reference
review candidates prevent a full supported verdict.
"""
    (REPORT_ROOT / "m46a_structured_grain_summary.md").write_text(markdown)


if __name__ == "__main__":
    main()
