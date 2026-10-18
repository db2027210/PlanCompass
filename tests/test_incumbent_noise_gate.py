import json
import math

from incumbent_noise_gate import (
    build_baseline_score_noise_gate,
    incumbent_improvement_passes,
)


def test_noise_gate_uses_six_windows_and_excludes_qps(tmp_path) -> None:
    samples = tmp_path / "samples.jsonl"
    rows = []
    for second in range(180):
        window = second // 30
        rows.append(
            {
                "start_epoch": 1000.0 + second,
                "seconds": 1.0,
                "cpu_utilization": 0.4 + window * 0.01,
                "memory_utilization": 0.4,
                "io_utilization": 0.4,
                "db_qps": 1e9 if second % 2 else 1.0,
            }
        )
    samples.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    gate = build_baseline_score_noise_gate(
        baseline_summary={
            "formal_pg_resource": {"samples_path": str(samples)}
        },
        capability={"cpu": 1, "memory": 1, "io": 1},
        score_func=lambda utilization: utilization["cpu"],
    )
    assert gate["window_count"] == 6
    assert gate["qps_used"] is False
    assert gate["minimum_actual_score_delta"] > 0
    assert incumbent_improvement_passes(
        actual_score=1.0 + gate["minimum_actual_score_delta"] + 1e-6,
        baseline_score=1.0,
        noise_gate=gate,
    )
    assert not incumbent_improvement_passes(
        actual_score=1.0 + gate["minimum_actual_score_delta"],
        baseline_score=1.0,
        noise_gate=gate,
    )


def test_constant_baseline_has_zero_confidence_half_width(tmp_path) -> None:
    samples = tmp_path / "samples.jsonl"
    samples.write_text(
        "\n".join(
            json.dumps(
                {
                    "start_epoch": float(second),
                    "seconds": 1.0,
                    "cpu_utilization": 0.4,
                    "memory_utilization": 0.4,
                    "io_utilization": 0.4,
                }
            )
            for second in range(180)
        )
        + "\n",
        encoding="utf-8",
    )
    gate = build_baseline_score_noise_gate(
        baseline_summary={
            "formal_pg_resource": {"samples_path": str(samples)}
        },
        capability={"cpu": 1, "memory": 1, "io": 1},
        score_func=lambda utilization: sum(utilization.values()),
    )
    assert math.isclose(gate["minimum_actual_score_delta"], 0.0)
