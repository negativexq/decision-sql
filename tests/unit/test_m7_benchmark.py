from evaluation.m7_benchmark import benchmark_hash, build_benchmark, split_hash, validate_structure


def test_m7_benchmark_is_balanced_and_frozen_shape() -> None:
    cases = build_benchmark()
    validation = validate_structure(cases)

    assert validation["passed"] is True
    assert validation["case_count"] == 150
    assert validation["dev_count"] == 100
    assert validation["holdout_count"] == 50
    assert validation["route_counts"] == {"DIRECT": 120, "GOVERNED": 30}
    assert validation["naturalness_counts"] == {"NATURAL": 150, "BORDERLINE": 0}


def test_m7_hashes_are_deterministic() -> None:
    first = build_benchmark()
    second = build_benchmark()

    assert benchmark_hash(first) == benchmark_hash(second)
    assert split_hash(first, "dev") == split_hash(second, "dev")
    assert split_hash(first, "holdout") == split_hash(second, "holdout")
    assert benchmark_hash(first) == (
        "f169e3641d64bdb9f009bb97ee6f82e6589608579817ed948f2109ddf8921043"
    )
    assert split_hash(first, "dev") == (
        "59bd32b94939b4f1c2d77ab79cc9feaf4622358009073f9c16f7108eee3a014f"
    )
    assert split_hash(first, "holdout") == (
        "740ab83e8700877e3bbe7bee6bcd61611a03d92755231dafebb7270d762be0e5"
    )
    assert {case.case_id for case in first if case.split == "dev"}.isdisjoint(
        {case.case_id for case in first if case.split == "holdout"}
    )


def test_m7_references_are_nonempty_and_route_labels_are_evaluation_only() -> None:
    for case in build_benchmark():
        assert case.reference_sql_variants
        assert case.adjudication_note
        assert case.expected_route in {"DIRECT", "GOVERNED"}
