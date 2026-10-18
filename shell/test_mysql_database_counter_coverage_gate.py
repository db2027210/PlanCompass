from pathlib import Path

from shell.manual_mysql_sgd_r0_campaign import Campaign, LANES


def measurement(start_ms: int, end_ms: int) -> dict:
    return {
        "rolling_measurement_control": {
            "selected_observation": {
                "database_throughput": {
                    "requested_start_epoch_ms": start_ms,
                    "requested_end_epoch_ms": end_ms,
                }
            }
        }
    }


def campaign_with_successes(successes: dict[int, list[int]]) -> Campaign:
    campaign = Campaign(LANES["tpch"], Path("/tmp/not-used"), "coverage-test")
    campaign.database_attempt_rows = [
        {
            "epoch_ms": epoch_ms,
            "sampler_index": sampler_index,
            "succeeded": True,
            "error_count": 0,
        }
        for sampler_index, epochs in successes.items()
        for epoch_ms in epochs
    ]
    return campaign


def test_database_counter_gate_passes_complete_staggered_sampling() -> None:
    start, end = 1_000_000, 1_020_000
    gate = campaign_with_successes({
        0: list(range(start + 1_000, end + 1, 1_000)),
        1: list(range(start + 500, end, 1_000)) + [end],
    }).database_counter_coverage_gate(measurement(start, end))
    assert gate["passed"] is True


def test_database_counter_gate_fails_when_one_sampler_loses_over_ten_percent() -> None:
    start, end = 1_000_000, 1_020_000
    gate = campaign_with_successes({
        0: list(range(start + 1_000, end + 1, 1_000)),
        1: list(range(start + 4_000, end + 1, 1_000)),
    }).database_counter_coverage_gate(measurement(start, end))
    assert gate["passed"] is False
    assert gate["samplers"]["1"]["coverage"] < 0.90
