#!/usr/bin/env python3
"""Tests for the SHA-pinned formal direction-ranker contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parameter_optimizer import load_real_replay_direction_ranker_contract  # noqa: E402
from real_replay_direction_ranker import (  # noqa: E402
    DirectionRankerConfig,
    ReplayRankingSample,
    train_real_replay_direction_ranker,
)


def _artifact() -> dict:
    samples = []
    for scope_index in range(4):
        for candidate_index, movement in enumerate((-1.2, -0.8, -0.4, 0.4, 0.8, 1.2)):
            success = movement > 0.0
            samples.append(
                ReplayRankingSample(
                    sample_id=f"s{scope_index}-{candidate_index}",
                    scope_id=f"scope-{scope_index}",
                    parameter_log_delta={"p": movement},
                    db_qps_delta_pct=1.0 if success else -1.0,
                    target_util_delta_pp=-1.0 if success else 1.0,
                    scenario_features={
                        "workload": "synthetic",
                        "target_resource": "cpu",
                        "scenario": "cpu",
                    },
                    plan_features={"plan_signature_available": success},
                )
            )
    result = train_real_replay_direction_ranker(
        samples,
        config=DirectionRankerConfig(max_group_folds=4),
    )
    assert result.ready, result.fail_closed_reasons
    artifact = result.to_dict()
    artifact["training_contract"] = {
        "workload": "synthetic",
        "target_resource": "cpu",
        "scenario": "cpu",
        "active_params": ["p"],
        "require_stable_measurement": True,
        "online_experience_table_mutation_allowed": False,
    }
    return artifact


def _config(path: Path, sha256: str) -> dict:
    return {
        "optimizer": {
            "gradient": {
                "real_replay_direction_ranker": {
                    "required": True,
                    "enabled": True,
                    "artifact_path": str(path),
                    "artifact_sha256": sha256,
                    "expected_artifact_type": "real_replay_direction_ranker_group_holdout_v1",
                    "expected_schema_version": 1,
                    "expected_workload": "synthetic",
                    "expected_target_resource": "cpu",
                    "expected_scenario": "cpu",
                    "admission": {
                        "max_joint_pairwise_inversion_rate": 0.10,
                        "min_joint_pair_count": 10,
                        "min_evaluable_ranking_scopes": 2,
                        "min_direction_accuracy_wilson_lower": 0.50,
                        "min_joint_top1_success_wilson_lower": 0.50,
                    },
                    "policy": {
                        "ranking_score": "joint_direction_success_probability",
                        "block_estimated_ranking_when_not_ready": True,
                        "block_real_replay_when_not_ready": True,
                        "block_online_t_update_when_not_ready": True,
                        "online_t_mutation": False,
                    },
                }
            }
        }
    }


class RealReplayDirectionRankerContractTests(unittest.TestCase):
    def test_valid_sha_pinned_artifact_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ranker.json"
            payload = json.dumps(_artifact(), sort_keys=True).encode("utf-8")
            path.write_bytes(payload)
            ranker, binding = load_real_replay_direction_ranker_contract(
                _config(path, hashlib.sha256(payload).hexdigest()),
                str(Path(tmp) / "config.json"),
                ["p"],
            )
            self.assertIsNotNone(ranker)
            self.assertTrue(binding["ready"])

    def test_not_ready_artifact_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ranker.json"
            artifact = _artifact()
            artifact["ready"] = False
            artifact["reason"] = "insufficient_positive"
            artifact["model"] = None
            payload = json.dumps(artifact, sort_keys=True).encode("utf-8")
            path.write_bytes(payload)
            with self.assertRaisesRegex(ValueError, "fail_closed"):
                load_real_replay_direction_ranker_contract(
                    _config(path, hashlib.sha256(payload).hexdigest()),
                    str(Path(tmp) / "config.json"),
                    ["p"],
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
