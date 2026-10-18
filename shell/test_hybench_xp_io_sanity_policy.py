#!/usr/bin/env python3
"""Fail-closed tests for the campaign-scoped HyBench XP IO sanity policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / (
    "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
    "hybench_xp_io_model_sanity_policy_v1.json"
)
POLICY_SHA256 = "88d48a783b827e2748bf88367c78dea65c401b80c6430ef41a8560ad2d01928f"
EXPECTED_SCOPE = {
    "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
    "benchmark": "hybench_xp",
    "lane_id": "hybench_xp_compute00_59630",
    "target_resource": "io",
}
EXPECTED_MINIMUMS = {"cpu": 0.05, "memory": 0.05, "io": 0.0048}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(policy: Dict[str, Any], scope: Dict[str, Any], observation: Dict[str, Any]) -> bool:
    """Return False for every malformed or out-of-scope observation."""
    if scope != policy.get("scope"):
        return False
    if not isinstance(observation, dict):
        return False
    if set(policy.get("required_observation_fields") or []) - set(observation):
        return False
    resource = observation.get("resource")
    minimums = policy.get("minimum_by_resource")
    if not isinstance(minimums, dict) or resource not in minimums:
        return False
    scale = observation.get("baseline_anchor_delta_scale")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        return False
    numeric_scale = float(scale)
    if not math.isfinite(numeric_scale) or numeric_scale < 0.0:
        return False
    minimum = minimums.get(resource)
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
        return False
    numeric_minimum = float(minimum)
    if not math.isfinite(numeric_minimum) or numeric_minimum < 0.0:
        return False
    return numeric_scale >= numeric_minimum


def validate_config(config_path: Path, policy: Dict[str, Any]) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    policy_binding = config.get("sanity_policy_contract")
    assert isinstance(policy_binding, dict)
    assert policy_binding == {"path": str(POLICY_PATH), "sha256": POLICY_SHA256}
    amendment_binding = config.get("post_evidence_amendment_contract")
    assert isinstance(amendment_binding, dict)
    amendment_path = Path(str(amendment_binding.get("path") or ""))
    assert amendment_path.is_file()
    assert sha256(amendment_path) == amendment_binding.get("sha256")
    minimums = config["metrics"]["plan_estimator"]["model_retraining"]["quality_gate"][
        "baseline_sanity"
    ]["anchor_delta_scale_min_by_resource"]
    assert minimums == EXPECTED_MINIMUMS
    gradient = config["optimizer"]["gradient"]
    assert gradient["eta"] == 0.0051
    assert gradient["max_eta"] == 0.0051
    assert gradient["target_bottleneck_resources"] == ["io"]
    assert config["_generated_from"]["target_resource"] == "io"
    attempt_contracts = [
        value
        for key, value in config.items()
        if key.startswith("_attempt") and key.endswith("_contract")
    ]
    assert len(attempt_contracts) == 1
    assert attempt_contracts[0]["scope"] == EXPECTED_SCOPE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()

    assert POLICY_PATH.is_file()
    assert sha256(POLICY_PATH) == POLICY_SHA256
    policy: Dict[str, Any] = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    assert policy["scope"] == EXPECTED_SCOPE
    assert policy["minimum_by_resource"] == EXPECTED_MINIMUMS

    assert evaluate(policy, EXPECTED_SCOPE, {"resource": "io", "baseline_anchor_delta_scale": 0.0048})
    assert not evaluate(
        policy,
        EXPECTED_SCOPE,
        {"resource": "io", "baseline_anchor_delta_scale": 0.004799999999999999},
    )
    assert not evaluate(policy, EXPECTED_SCOPE, {"resource": "io"})
    assert not evaluate(policy, EXPECTED_SCOPE, {"baseline_anchor_delta_scale": 0.005})
    assert not evaluate(
        policy, EXPECTED_SCOPE, {"resource": "io", "baseline_anchor_delta_scale": float("nan")}
    )
    assert not evaluate(
        policy, EXPECTED_SCOPE, {"resource": "io", "baseline_anchor_delta_scale": float("inf")}
    )
    assert not evaluate(policy, EXPECTED_SCOPE, {"resource": "io", "baseline_anchor_delta_scale": -1.0})
    assert not evaluate(policy, EXPECTED_SCOPE, {"resource": "cpu", "baseline_anchor_delta_scale": 0.049})
    assert evaluate(policy, EXPECTED_SCOPE, {"resource": "cpu", "baseline_anchor_delta_scale": 0.05})
    assert not evaluate(policy, EXPECTED_SCOPE, {"resource": "memory", "baseline_anchor_delta_scale": 0.049})
    assert evaluate(policy, EXPECTED_SCOPE, {"resource": "memory", "baseline_anchor_delta_scale": 0.05})
    for key in EXPECTED_SCOPE:
        wrong_scope = dict(EXPECTED_SCOPE)
        wrong_scope[key] = f"wrong-{EXPECTED_SCOPE[key]}"
        assert not evaluate(policy, wrong_scope, {"resource": "io", "baseline_anchor_delta_scale": 1.0})

    if args.config:
        validate_config(args.config.resolve(), policy)

    print("HyBench XP campaign-scoped IO sanity policy tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
