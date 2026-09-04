from evaluation.m7_benchmark import build_benchmark
from evaluation.run_m7 import adjudicate_failures


def test_routing_is_earliest_cause_for_false_governed_result() -> None:
    case = next(case for case in build_benchmark() if case.expected_route == "DIRECT")
    rows = [
        {
            "case_id": case.case_id,
            "family": case.family,
            "correct": False,
            "route_actual": "GOVERNED",
            "failure": "RESULT_MISMATCH",
            "status": "SUCCEEDED",
        }
    ]

    result = adjudicate_failures((case,), rows)

    assert result["counts"] == {"ROUTING_ERROR": 1}
    assert result["records"][0]["adjudication_status"] == "STRONGLY_SUPPORTED"


def test_provider_and_m1_failures_are_not_result_mismatch_causes() -> None:
    cases = build_benchmark()
    first = cases[0]
    second = next(case for case in cases if case.expected_route == "DIRECT")
    rows = [
        {
            "case_id": first.case_id,
            "family": first.family,
            "correct": False,
            "failure": "PROVIDER_PROTOCOL_ERROR",
            "status": "SQL_GENERATION_ERROR",
        },
        {
            "case_id": second.case_id,
            "family": second.family,
            "correct": False,
            "failure": "M1_POLICY_REJECTION",
            "status": "PLAN_REJECTED",
        },
    ]

    result = adjudicate_failures((first, second), rows)

    assert result["counts"] == {
        "PROVIDER_PROTOCOL_ERROR": 1,
        "M1_POLICY_REJECTION": 1,
    }
