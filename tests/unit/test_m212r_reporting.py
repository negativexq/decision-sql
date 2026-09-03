from evaluation.run_m212r import _summary


def test_m212r_summary_uses_ir_parse_and_policy_fields() -> None:
    summary = _summary(
        [
            {
                "json_parse_success": True,
                "plan_accepted": False,
                "execution_success": False,
                "failure_taxonomy": "POLICY_REJECTION",
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "generation_latency_ms": 5,
            },
            {
                "json_parse_success": False,
                "plan_accepted": False,
                "execution_success": False,
                "failure_taxonomy": "DTO_VALIDATION_FAILED",
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "generation_latency_ms": 7,
            },
        ]
    )

    assert summary["parse_success"] == 1
    assert summary["policy_rejections"] == 1


def test_m212r_summary_keeps_baseline_fields_compatible() -> None:
    summary = _summary(
        [
            {
                "parse_success": True,
                "plan_accepted": True,
                "execution_success": True,
                "failure_stage": None,
                "result_equivalent": True,
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "generation_latency_ms": 5,
            }
        ]
    )

    assert summary["parse_success"] == 1
    assert summary["policy_rejections"] == 0
