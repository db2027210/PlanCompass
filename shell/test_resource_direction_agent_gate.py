#!/usr/bin/env python3
"""Tests for the Agent/model resource-direction agreement gate."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from resource_direction_agent_gate import evaluate_agent_model_direction_agreement


MODEL = {
    "cpu": {"low": -3.0, "p50": -2.0, "high": -1.0},
    "memory": {"low": 0.2, "p50": 0.5, "high": 0.8},
    "io": {"low": -1.0, "p50": -0.5, "high": -0.1},
}


def main() -> None:
    agreed = evaluate_agent_model_direction_agreement(
        {
            "direction": {"cpu": "decrease", "memory": "increase", "io": "decrease"},
            "confidence": {"cpu": 0.9, "memory": 0.8, "io": 0.75},
            "out_of_distribution": False,
        },
        MODEL,
    )
    assert agreed["optimistic_interval_pruning_allowed"] is True
    mismatch = evaluate_agent_model_direction_agreement(
        {
            "direction": {"cpu": "increase", "memory": "increase", "io": "decrease"},
            "confidence": {"cpu": 0.9, "memory": 0.8, "io": 0.75},
        },
        MODEL,
    )
    assert mismatch["optimistic_interval_pruning_allowed"] is False
    uncertain_model = dict(MODEL)
    uncertain_model["io"] = {"low": -1.0, "p50": -0.2, "high": 0.4}
    uncertain = evaluate_agent_model_direction_agreement(
        {
            "direction": {"cpu": "decrease", "memory": "increase", "io": "decrease"},
            "confidence": {"cpu": 0.9, "memory": 0.8, "io": 0.75},
        },
        uncertain_model,
    )
    assert uncertain["optimistic_interval_pruning_allowed"] is False
    assert uncertain["qps_used"] is False
    print("resource direction Agent/model agreement tests passed", flush=True)


if __name__ == "__main__":
    main()
