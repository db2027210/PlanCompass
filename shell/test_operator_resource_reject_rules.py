#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import partition_operator_rule_rejected_candidates
from operator_resource_rules import (
    build_rule_artifact,
    evaluate_rule_gate,
    load_rule_gate,
    sha256_file,
)


def probe(seq_delta: float = 1.0, index_delta: float = -1.0) -> dict:
    changes = []
    if seq_delta:
        changes.append(
            {
                "operator": "plan_seq_scan_count",
                "baseline": 1.0,
                "candidate": 1.0 + seq_delta,
                "delta": seq_delta,
                "direction": "increase" if seq_delta > 0 else "decrease",
            }
        )
    if index_delta:
        changes.append(
            {
                "operator": "plan_index_scan_count",
                "baseline": 2.0,
                "candidate": 2.0 + index_delta,
                "delta": index_delta,
                "direction": "increase" if index_delta > 0 else "decrease",
            }
        )
    return {
        "status": "ok",
        "operator_changes": {"changes": changes},
    }


def validation_row(index: int, *, technical_failure: bool = False) -> dict:
    return {
        "event": "candidate_validation",
        "target": "memory",
        "status": "failed" if technical_failure else "ok",
        "returncode": 1 if technical_failure else 0,
        "round": (index % 5) + 1,
        "candidate_index": index,
        "params": {"seq_page_cost": float(index)},
        "candidate_plan_fingerprint": f"plan-{index}",
        "comparison_baseline_mode": "same_worker",
        "comparison_baseline_matched": True,
        "resource_stats": {"resource_monitor_ok": True},
        "real_score_c": {
            "valid": True,
            "target_drop_ratio": 0.25 if technical_failure else -0.02,
        },
        "plan_input_changes": probe(),
        "db_qps": -1000000.0 if index % 2 else 1000000.0,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="operator-rule-test-") as raw_tmp:
        tmp = Path(raw_tmp)
        plan_sql = tmp / "plan.sql"
        plan_sql.write_text("SELECT 1;\n", encoding="utf-8")
        logs = []
        for log_index in range(2):
            log_path = tmp / f"run-{log_index}.jsonl"
            start = log_index * 10
            rows = [validation_row(start + offset + 1) for offset in range(10)]
            rows.append(validation_row(100 + log_index, technical_failure=True))
            log_path.write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )
            logs.append(log_path)

        contract_id = "synthetic-job-memory"
        manifest = {
            "contracts": [
                {
                    "contract_id": contract_id,
                    "workload": "job",
                    "target": "memory",
                    "plan_sql_path": str(plan_sql),
                }
            ],
            "sources": [
                {"contract_id": contract_id, "path": str(path)} for path in logs
            ],
        }
        artifact = build_rule_artifact(manifest)
        contract = artifact["contracts"][contract_id]
        assert contract["usable_real_replay_points"] == 20
        assert contract["trusted_rule_count"] == 1
        assert artifact["policy"]["qps_used"] is False
        assert all(
            source["event_counts"].get("technical_or_workload_failure") == 1
            for source in artifact["sources"]
        )

        artifact_path = tmp / "artifact.json"
        artifact_path.write_text(json.dumps(artifact, sort_keys=True), encoding="utf-8")
        gate = load_rule_gate(
            {
                "enabled": True,
                "artifact_path": str(artifact_path),
                "artifact_sha256": sha256_file(artifact_path),
                "contract_id": contract_id,
            },
            workload="job",
            target="memory",
            plan_sql_path=plan_sql,
        )
        exact = evaluate_rule_gate(probe(), gate)
        assert exact["rejected"] is True
        assert exact["qps_used"] is False
        assert exact["state_updates_permitted"] == {
            "accepted": False,
            "incumbent": False,
            "range": False,
            "gradient": False,
        }
        assert evaluate_rule_gate(probe(index_delta=0.0), gate)["rejected"] is False
        assert evaluate_rule_gate(probe(seq_delta=4.0, index_delta=-4.0), gate)["rejected"] is False

        ranked = []
        for index in range(1, 7):
            candidate = {"raw_candidate_index": index, "signature": f"sig-{index}"}
            candidate_probe = probe() if index == 1 else {"status": "ok", "operator_changes": {"changes": []}}
            ranked.append((index, candidate, candidate_probe))
        eligible, rejected = partition_operator_rule_rejected_candidates(
            copy.deepcopy(ranked),
            gate,
        )
        assert [raw_idx for raw_idx, _, _ in eligible[:5]] == [2, 3, 4, 5, 6]
        assert len(rejected) == 1 and rejected[0][0] == 1
        assert eligible[0][1]["operator_rule_pre_gate_rank"] == 2

    print("operator resource reject rule tests: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
