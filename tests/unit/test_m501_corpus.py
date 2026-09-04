import json
from pathlib import Path

from sqlglot import parse_one

from app.verification import RULESET_HASH, DeterministicSemanticVerifier
from evaluation.m4_benchmark import build_benchmark
from evaluation.m501_corpus import (
    CORPUS_ID,
    CORPUS_VERSION,
    ExpectedSemanticStatus,
    build_corpus,
    corpus_hash,
    split_hash,
    validate_structure,
)

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_CORPUS_HASH = "7cee1b1ca6ccd856a46a1fd17c0c9a1f7765fdec060bfbeef7b149a51e1e60c7"
EXPECTED_DEV_HASH = "85f7424a363261e670a56ea0e441181eca6e409e3e9ce14e0c5c2959ddcc011a"
EXPECTED_HOLDOUT_HASH = "56e4cdd96a924431dd076e381e1f0ad332fe88249929660dd0bc205282edba3b"
EXPECTED_RULESET_HASH = "4b3c91168832f1d3ca4370972d33abfebc7158712ea7ac04c8ea687d2253b494"


def test_m501_corpus_manifest_is_frozen_and_balanced() -> None:
    cases = build_corpus()
    manifest = json.loads((ROOT / "evaluation/fixtures/m501_manifest.json").read_text())
    structure = validate_structure(cases)

    assert manifest["corpus_id"] == CORPUS_ID
    assert manifest["corpus_version"] == CORPUS_VERSION
    assert structure["total"] == manifest["total"] == 240
    assert structure["dev"] == manifest["dev"] == 192
    assert structure["holdout"] == manifest["holdout"] == 48
    assert structure["correct"] == manifest["correct"] == 120
    assert structure["incorrect"] == manifest["incorrect"] == 120
    assert corpus_hash(cases) == manifest["corpus_hash"] == EXPECTED_CORPUS_HASH
    assert split_hash(cases, "DEV") == manifest["dev_hash"] == EXPECTED_DEV_HASH
    assert split_hash(cases, "HOLDOUT") == manifest["holdout_hash"] == EXPECTED_HOLDOUT_HASH
    assert [case.case_id for case in cases] == [item["case_id"] for item in manifest["cases"]]
    assert [case.family for case in cases] == [item["family"] for item in manifest["cases"]]
    assert [case.expected_semantic_status for case in cases] == [
        item["expected_status"] for item in manifest["cases"]
    ]
    assert all(values == {
        "correct": 10,
        "dev": 16,
        "holdout": 4,
        "incorrect": 10,
        "paired": 10,
        "total": 20,
    } for values in structure["family_counts"].values())


def test_m501_pairs_ids_and_splits_are_disjoint() -> None:
    cases = build_corpus()
    by_id = {case.case_id: case for case in cases}

    assert len(by_id) == len(cases)
    assert {case.question for case in cases if case.split == "DEV"}.isdisjoint(
        {case.question for case in cases if case.split == "HOLDOUT"}
    )
    for case in cases:
        assert case.paired_case_id is not None
        paired = by_id[case.paired_case_id]
        assert paired.paired_case_id == case.case_id
        assert paired.family == case.family
        assert paired.question == case.question
        assert paired.split == case.split
        assert case.expected_semantic_status != paired.expected_semantic_status


def test_m501_cases_and_references_parse_without_provider_or_database() -> None:
    for case in build_corpus():
        parse_one(case.candidate_sql, read="postgres")
        assert case.gold_reference_sql is not None
        parse_one(case.gold_reference_sql, read="postgres")
        assert case.expected_semantic_status in {
            ExpectedSemanticStatus.CORRECT,
            ExpectedSemanticStatus.INCORRECT,
        }


def test_m501_does_not_reuse_m4_holdout_questions_or_sql() -> None:
    cases = build_corpus()
    m4_holdout = [question for question in build_benchmark() if question.split == "holdout"]
    m4_questions = {question.question for question in m4_holdout}
    m4_sql = {question.gold_sql.strip() for question in m4_holdout}
    m501_sql = set()
    for case in cases:
        assert case.gold_reference_sql is not None
        m501_sql.add(case.gold_reference_sql.strip())

    assert not ({case.question for case in cases} & m4_questions)
    assert not ({case.candidate_sql.strip() for case in cases} & m4_sql)
    assert not (m501_sql & m4_sql)


def test_m501_repeated_builds_are_identical() -> None:
    first = build_corpus()
    second = build_corpus()

    assert first == second
    assert corpus_hash(first) == corpus_hash(second)
    assert split_hash(first, "DEV") == split_hash(second, "DEV")
    assert split_hash(first, "HOLDOUT") == split_hash(second, "HOLDOUT")


def test_m501_evaluation_keeps_verifier_ruleset_frozen() -> None:
    assert RULESET_HASH == EXPECTED_RULESET_HASH
    assert DeterministicSemanticVerifier().verify(
        "List orders.", "SELECT id FROM orders"
    ).ruleset_hash == EXPECTED_RULESET_HASH


def test_m501_runtime_verifier_boundary_excludes_adjudication_fields() -> None:
    verifier = DeterministicSemanticVerifier()

    # The production verifier accepts only runtime-legitimate inputs.  Gold
    # status, reference SQL, and adjudication notes are evaluation-only.
    report = verifier.verify("List orders.", "SELECT id FROM orders")
    assert report is not None
    try:
        verifier.verify(  # type: ignore[call-arg]
            "List orders.",
            "SELECT id FROM orders",
            expected_status=ExpectedSemanticStatus.CORRECT,
            reference_sql="SELECT id FROM orders",
            adjudication_note="evaluation-only",
        )
    except TypeError:
        pass
    else:
        raise AssertionError("production verifier accepted evaluation-only labels")
