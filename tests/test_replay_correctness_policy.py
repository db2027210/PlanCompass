from replay_correctness_policy import classify_transaction_abort_gate


def _summary(**overrides):
    payload = {
        "status": "complete",
        "stop_reason": "stable_early_stop",
        "native_sql_exception_occurrences": 0,
        "native_tps": 500.0,
        "effective_measurement_seconds": 180.0,
    }
    payload.update(overrides)
    return payload


def test_clean_complete_payload_is_valid_success() -> None:
    decision = classify_transaction_abort_gate(_summary(), "")
    assert decision["valid_replay"] is True
    assert decision["algorithmic_gate_rejection"] is False
    assert decision["technical_failure"] is False


def test_expected_abort_is_algorithmic_and_consumes_replay_elsewhere() -> None:
    decision = classify_transaction_abort_gate(
        _summary(
            status="failed",
            native_sql_exception_occurrences=1,
        ),
        "ERROR SQL State [40P01]",
    )
    assert decision["valid_replay"] is True
    assert decision["algorithmic_gate_rejection"] is True
    assert decision["technical_failure"] is False


def test_unexpected_or_mismatched_sql_error_is_technical() -> None:
    unexpected = classify_transaction_abort_gate(
        _summary(
            status="failed",
            native_sql_exception_occurrences=1,
        ),
        "ERROR SQL State [XX000]",
    )
    mismatch = classify_transaction_abort_gate(
        _summary(
            status="failed",
            native_sql_exception_occurrences=2,
        ),
        "ERROR SQL State [40P01]",
    )
    assert unexpected["technical_failure"] is True
    assert mismatch["technical_failure"] is True
