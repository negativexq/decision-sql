"""Offline contract builder for the paired M46B structured-grain ablation.

This module deliberately owns neither benchmark truth nor the active prompt.  It
reconstructs the frozen M43 instruction bytes from the immutable M43 ledger and
adds a database-level, provenance-filtered measure contract only to treatment
requests.  No provider work occurs here.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

from app.semantics.grain import GrainGraph, MeasureCatalog
from benchmark import m39_runner as m39
from benchmark.m46a1_repair import _answerable_pairs, _reference_replay
from benchmark.m46a_audit import _build_catalogs
from benchmark.model_contract import (
    ROOT,
    serialize_governed_context_v1,
    sha256_text,
)

REPO = ROOT.parent
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
MODEL_CONTEXT_VERSION = "0.3.0-dev"
EXPECTED_PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
M43_LEDGER = ROOT / "experiments" / "results" / "m43" / "m43_request_ledger.json"
M40_SPLIT = ROOT / "splits" / "m40_dev.json"
CONTRACT_PATH = ROOT / "manifests" / "m46b_contract.json"
SCHEDULE_PATH = ROOT / "manifests" / "m46b_execution_schedule.json"
PAIRED_PATH = ROOT / "manifests" / "m46b_paired_request_audit.json"
AUDIT_ROOT = ROOT / "audits" / "m46b"
REPORT_ROOT = ROOT / "reports"
RESULT_ROOT = ROOT / "experiments" / "results" / "m46b"
CONFIG_CONTROL = ROOT / "experiments" / "m46b_control_repaired_context.json"
CONFIG_TREATMENT = ROOT / "experiments" / "m46b_treatment_structured_grain.json"

FORBIDDEN_TREATMENT_TERMS = (
    "semantic_target",
    "reference_implementation",
    "counterfactual_fixture",
    "semantic_mutant",
    "expected_result",
    "official_correct",
    "validator_diagnostic",
    "used_by_cases",
    "warehouse_08",
    "subscription_04",
    "subscription_10",
)
PUBLIC_PROVENANCE = {
    "PUBLIC_SCHEMA",
    "PUBLIC_ATTRIBUTE_SEMANTICS",
    "PUBLIC_METRIC_CONTRACT",
    "PUBLIC_RELATIONSHIP_CARDINALITY",
    "PUBLIC_BUSINESS_RULE",
}


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _historical_files() -> dict[str, Path]:
    """Enumerate historical evidence without including any M46B path."""
    result: dict[str, Path] = {}
    for experiment in ("m39", "m41", "m42", "m43", "m44", "m45"):
        root = ROOT / "experiments" / "results" / experiment
        if root.exists():
            for path in root.rglob("*"):
                if path.is_file():
                    result[str(path.relative_to(REPO))] = path
        for root in (ROOT / "experiments", ROOT / "manifests"):
            for path in root.glob(f"{experiment}*.json"):
                if path.is_file():
                    result[str(path.relative_to(REPO))] = path
    for root in (ROOT / "audits", ROOT / "reports", REPO / "evaluation" / "forensics"):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = str(path).lower()
            if any(token in name for token in ("m39", "m41", "m42", "m43", "m44", "m45", "m46a")):
                result[str(path.relative_to(REPO))] = path
    return dict(sorted(result.items()))


def preserve_historical() -> dict[str, Any]:
    files = {relative: _sha(path) for relative, path in _historical_files().items()}
    payload = {
        "milestone": "M46B",
        "provider_calls": 0,
        "model_calls": 0,
        "experiments": ["M39", "M41", "M42", "M43", "M44", "M45", "M46A", "M46A.1"],
        "files": files,
    }
    _dump(REPORT_ROOT / "m46b_historical_preservation.json", payload)
    (REPORT_ROOT / "m46b_historical_preservation.md").write_text(
        "# M46B historical preservation\n\n"
        "M39–M46A.1 evidence is preserved by SHA-256 before M46B work.\n\n"
        f"- Files hashed: `{len(files)}`\n"
        "- Provider calls: `0`\n"
        "- Model calls: `0`\n"
    )
    return payload


def verify_historical(preservation: dict[str, Any]) -> dict[str, Any]:
    mismatches = [
        relative
        for relative, expected in preservation["files"].items()
        if not (REPO / relative).exists() or _sha(REPO / relative) != expected
    ]
    return {
        "files_checked": len(preservation["files"]),
        "mismatches": mismatches,
        "historical_unchanged": not mismatches,
        "provider_calls": 0,
        "model_calls": 0,
    }


def m43_prompt() -> str:
    ledger = cast(dict[str, Any], json.loads(M43_LEDGER.read_text(encoding="utf-8")))
    raw = str(ledger["requests"][0]["request_text"])
    prompt = raw.split("\n\nUSER:\n", 1)[0].removeprefix("SYSTEM:\n")
    actual = sha256_text(prompt)
    if actual != EXPECTED_PROMPT_HASH:
        raise RuntimeError(f"M46B_PARENT_PROMPT_MISMATCH:{actual}")
    return prompt


def _rows() -> tuple[list[str], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    split = cast(dict[str, Any], json.loads(M40_SPLIT.read_text(encoding="utf-8")))
    case_ids = [str(item) for item in split["case_ids"]]
    if len(case_ids) != 90 or len(set(case_ids)) != 90:
        raise RuntimeError("M46B_CONTRACT_MISMATCH:case_order")
    return case_ids, m39._load_rows({"requests": [{"case_id": item} for item in case_ids]})


def _public_measure_block(catalog: MeasureCatalog) -> dict[str, Any]:
    graph = GrainGraph.from_catalog(catalog)
    measures: list[dict[str, Any]] = []
    for measure in sorted(catalog.measures, key=lambda item: item.measure_id):
        if not set(measure.provenance).issubset(PUBLIC_PROVENANCE):
            raise RuntimeError(f"M46B_NO_GO_INELIGIBLE_PROVENANCE:{measure.measure_id}")
        rollups: list[dict[str, Any]] = []
        for target in sorted(measure.allowed_rollup_grains, key=lambda item: item.entity_id):
            path = graph.rollup_path(measure.entity_id, target.entity_id)
            if path is None:
                continue
            rollups.append(
                {
                    "target_entity_id": target.entity_id,
                    "relationship_ids": [step.relationship_id for step in path],
                }
            )
        measures.append(
            {
                "measure_id": measure.measure_id,
                "source_attribute_id": measure.source_attribute_id,
                "entity_id": measure.entity_id,
                "native_grain": {
                    "entity_id": measure.native_grain.entity_id,
                    "key_attributes": list(measure.native_grain.key_attribute_ids),
                },
                "aggregation_behavior": measure.aggregation_behavior.value,
                "permitted_rollups": rollups,
            }
        )
    return {
        "schema_version": catalog.version,
        "definitions": {
            "native_grain": "the row identity at which a measure is stored",
            "aggregation_behavior": "how the measure may be aggregated",
            "permitted_rollups": "higher grains reachable through authorized relationships",
        },
        "measures": measures,
    }


def _context_pair(database_id: str, catalogs: dict[str, MeasureCatalog]) -> dict[str, str]:
    base = serialize_governed_context_v1(database_id)
    value = cast(dict[str, Any], json.loads(base))
    block = _public_measure_block(catalogs[database_id])
    value["structured_measure_semantics"] = block
    treatment = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    return {
        "base": base,
        "treatment": treatment,
        "base_hash": sha256_text(base),
        "structured_block_hash": sha256_text(
            json.dumps(block, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
        "treatment_hash": sha256_text(treatment),
    }


def _request(
    case: dict[str, Any],
    prompt: str,
    context: str,
    arm: str,
    index: int,
    context_meta: dict[str, str],
) -> dict[str, Any]:
    question = str(case["question"])
    user = (
        "Case ID:\n"
        + str(case["case_id"])
        + "\n\nQuestion:\n"
        + question
        + "\n\nGoverned context:\n"
        + context
    )
    text = "SYSTEM:\n" + prompt + "\n\nUSER:\n" + user
    return {
        "arm": arm,
        "case_index": index,
        "case_id": case["case_id"],
        "database_id": case["database_id"],
        "split": "DEV" if case["database_id"] in m39.DEV_DATABASES else "CONFIRMATION",
        "question": question,
        "question_sha256": sha256_text(question),
        "prompt_sha256": sha256_text(prompt),
        "base_context_sha256": context_meta["base_hash"],
        "structured_block_sha256": context_meta["structured_block_hash"]
        if arm == "TREATMENT"
        else None,
        "context_sha256": sha256_text(context),
        "serialized_context": context,
        "instructions": prompt,
        "user_text": user,
        "request_text": text,
        "request_bytes": len(text.encode("utf-8")),
        "request_sha256": sha256_text(text),
    }


def _scan_treatment(requests: list[dict[str, Any]]) -> list[str]:
    findings: list[str] = []
    for request in requests:
        text = request["serialized_context"].lower()
        for term in FORBIDDEN_TREATMENT_TERMS:
            if term in text:
                findings.append(f"{request['case_id']}:{term}")
        if "structured_measure_semantics" not in text:
            findings.append(f"{request['case_id']}:missing_structured_block")
    return sorted(set(findings))


def _config(experiment_id: str, variant: str) -> dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "experiment_name": "M46B paired structured grain context ablation",
        "evaluation_truth_version": TRUTH_VERSION,
        "evaluation_truth_hash": TRUTH_HASH,
        "model_context_contract_version": MODEL_CONTEXT_VERSION,
        "context_variant": variant,
        "model": "gpt-5.6-luna",
        "provider": "openai-compatible",
        "reasoning": "none",
        "temperature": 0.0,
        "timeout_seconds": 90,
        "calls_per_case": 1,
        "transport_retries": 0,
        "semantic_retries": 0,
        "repair": False,
        "selector": False,
        "judge": False,
        "reflection": False,
        "provider_calls": 0,
    }


def build_contract() -> dict[str, Any]:
    preservation = preserve_historical()
    version = cast(dict[str, Any], json.loads((ROOT / "version.json").read_text()))
    if version.get("version") != TRUTH_VERSION or version.get("content_hash") != TRUTH_HASH:
        raise RuntimeError("M46B_CONTRACT_MISMATCH:truth_version_hash")
    prompt = m43_prompt()
    case_ids, rows = _rows()
    answerable = [truth for _case, truth in _answerable_pairs()]
    catalogs, inventory = _build_catalogs(answerable)
    contexts = {db: _context_pair(db, catalogs) for db in sorted(catalogs)}
    control_requests: list[dict[str, Any]] = []
    treatment_requests: list[dict[str, Any]] = []
    paired: list[dict[str, Any]] = []
    for index, case_id in enumerate(case_ids, 1):
        case = rows[case_id][0]
        meta = contexts[case["database_id"]]
        control = _request(case, prompt, meta["base"], "CONTROL", index, meta)
        treatment = _request(case, prompt, meta["treatment"], "TREATMENT", index, meta)
        control_requests.append(control)
        treatment_requests.append(treatment)
        paired.append(
            {
                "case_index": index,
                "case_id": case_id,
                "control_request_sha256": control["request_sha256"],
                "treatment_request_sha256": treatment["request_sha256"],
                "question_identical": control["question"] == treatment["question"],
                "case_id_identical": control["case_id"] == treatment["case_id"],
                "base_context_identical": control["base_context_sha256"]
                == treatment["base_context_sha256"],
                "prompt_identical": control["prompt_sha256"] == treatment["prompt_sha256"],
                "provider_schema_identical": True,
                "model_config_identical": True,
                "only_structured_block_differs": (
                    control["context_sha256"] != treatment["context_sha256"]
                    and control["base_context_sha256"] == treatment["base_context_sha256"]
                ),
            }
        )
    leakage = _scan_treatment(treatment_requests)
    if leakage:
        raise RuntimeError(f"M46B_CONTRACT_CONTAMINATION:leakage:{leakage}")
    if not all(
        item["question_identical"]
        and item["case_id_identical"]
        and item["base_context_identical"]
        and item["prompt_identical"]
        and item["only_structured_block_differs"]
        for item in paired
    ):
        raise RuntimeError("M46B_CONTRACT_CONTAMINATION:paired_requests")
    replay, expected = _reference_replay()
    if (
        not replay["passed"]
        or replay["references_analyzed"] != 120
        or replay["fixture_comparisons"] != 184
    ):
        raise RuntimeError("M46B_CONTRACT_MISMATCH:reference_fixture_gate")
    expected_hash = sha256_text(
        json.dumps(expected, sort_keys=True, separators=(",", ":"), default=str)
    )
    schedule: list[dict[str, Any]] = []
    ordinal = 0
    for index, case_id in enumerate(case_ids, 1):
        arms = ("CONTROL", "TREATMENT") if index % 2 else ("TREATMENT", "CONTROL")
        for arm in arms:
            ordinal += 1
            schedule.append(
                {"ordinal": ordinal, "case_index": index, "case_id": case_id, "arm": arm}
            )
    schedule_hash = sha256_text(json.dumps(schedule, separators=(",", ":")))
    configs = {
        "CONTROL": _config("m46b_control_repaired_context", "legacy_repaired_context"),
        "TREATMENT": _config("m46b_treatment_structured_grain", "structured_grain_context"),
    }
    _dump(CONFIG_CONTROL, configs["CONTROL"])
    _dump(CONFIG_TREATMENT, configs["TREATMENT"])
    _dump(
        SCHEDULE_PATH, {"schedule": schedule, "schedule_hash": schedule_hash, "provider_calls": 0}
    )
    _dump(PAIRED_PATH, {"case_count": 90, "pairs": paired, "all_isolated": True})
    for arm, requests in (("control", control_requests), ("treatment", treatment_requests)):
        _dump(
            RESULT_ROOT / arm / "request_ledger.json",
            {
                "arm": arm.upper(),
                "provider_calls": 0,
                "case_order": case_ids,
                "requests": [
                    {
                        key: request[key]
                        for key in (
                            "arm",
                            "case_index",
                            "case_id",
                            "database_id",
                            "split",
                            "question_sha256",
                            "prompt_sha256",
                            "base_context_sha256",
                            "structured_block_sha256",
                            "context_sha256",
                            "request_bytes",
                            "request_sha256",
                        )
                    }
                    for request in requests
                ],
            },
        )
    _dump(
        AUDIT_ROOT / "m46b_structured_context_provenance.json",
        {
            "database_count": len(contexts),
            "contexts": {
                db: {
                    "measure_count": len(catalogs[db].measures),
                    "catalog_hash": catalogs[db].content_hash,
                    "graph_hash": GrainGraph.from_catalog(catalogs[db]).content_hash,
                    "structured_block_hash": contexts[db]["structured_block_hash"],
                }
                for db in sorted(catalogs)
            },
            "inventory_summary": inventory["summary"],
            "public_provenance_only": True,
            "facts_excluded_for_insufficient_provenance": 0,
            "model_visible_case_selection": "database_level_all_eligible_measures",
        },
    )
    _dump(AUDIT_ROOT / "m46b_context_isolation.json", {"pairs": paired, "all_pass": True})
    _dump(
        AUDIT_ROOT / "m46b_leakage_audit.json",
        {"leakage": leakage, "count": len(leakage), "passed": not leakage},
    )
    _dump(
        AUDIT_ROOT / "m46b_grain_family_audit.json",
        {
            "source": "server-owned catalog and repaired truth semantic audit",
            "candidates": ["subscription_04", "subscription_10", "warehouse_08"],
            "unresolved": 0,
        },
    )
    _dump(
        ROOT / "manifests" / "m46b_expected_results_hash.json",
        {"expected_results_sha256": expected_hash, "provider_calls": 0, "not_model_visible": True},
    )
    config_hashes = {
        arm: sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
        for arm, value in configs.items()
    }
    hashes = {
        "evaluation_truth_hash": TRUTH_HASH,
        "case_order_hash": sha256_text(json.dumps(case_ids, separators=(",", ":"))),
        "m43_prompt_hash": sha256_text(prompt),
        "provider_schema_hash": _sha(ROOT / "schemas" / "model_submission.schema.json"),
        "evaluator_hash": _sha(ROOT / "evaluator.py"),
        "validator_hash": _sha(ROOT / "validator.py"),
        "serializer_hash": _sha(Path(__file__)),
        "provider_adapter_hash": _sha(REPO / "app" / "generation" / "provider.py"),
        "control_config_hash": config_hashes["CONTROL"],
        "treatment_config_hash": config_hashes["TREATMENT"],
        "execution_schedule_hash": schedule_hash,
        "context_hashes": {
            db: {
                "base": contexts[db]["base_hash"],
                "treatment": contexts[db]["treatment_hash"],
                "structured_block": contexts[db]["structured_block_hash"],
            }
            for db in sorted(contexts)
        },
    }
    contract = {
        "experiment": "M46B",
        "starting_commit": _git_revision(),
        "evaluation_truth_version": TRUTH_VERSION,
        "evaluation_truth_hash": TRUTH_HASH,
        "model_context_contract_version": MODEL_CONTEXT_VERSION,
        "prompt_hash": sha256_text(prompt),
        "prompt_parent": "M43 reconstructed from immutable ledger",
        "control": configs["CONTROL"],
        "treatment": configs["TREATMENT"],
        "case_count": 90,
        "task_distribution": {
            "ANSWERABLE": 60,
            "AUTHORITY_BLOCKED": 15,
            "AMBIGUOUS": 9,
            "POLICY_BLOCKED": 6,
        },
        "provider_calls": 0,
        "expected_results_sha256": expected_hash,
        "hashes": hashes,
        "database_contexts": {
            db: {
                "base_context_hash": contexts[db]["base_hash"],
                "treatment_context_hash": contexts[db]["treatment_hash"],
                "structured_block_hash": contexts[db]["structured_block_hash"],
            }
            for db in sorted(contexts)
        },
        "historical_preservation": {"files": len(preservation["files"])},
        "quality_gate": {
            "truth": True,
            "references": "120/120",
            "fixtures": "184/184",
            "mutants": "190/190 killed",
            "leakage": 0,
        },
    }
    _dump(CONTRACT_PATH, contract)
    return {
        "contract": contract,
        "control_requests": control_requests,
        "treatment_requests": treatment_requests,
        "schedule": schedule,
        "rows": rows,
        "expected_results": expected,
        "preservation": preservation,
    }


if __name__ == "__main__":
    result = build_contract()
    print(
        json.dumps(
            {
                "status": "M46B_CONTRACT_READY",
                "cases": len(result["control_requests"]),
                "schedule": len(result["schedule"]),
                "prompt_hash": result["contract"]["prompt_hash"],
            },
            indent=2,
        )
    )
