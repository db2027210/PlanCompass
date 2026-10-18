#!/usr/bin/env python3
"""Build a two-lane semantic baseline carrying a checkpoint incumbent."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as verifier
from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as base


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def binding(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    return {"path": str(path), "sha256": base.alg.sha(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--scene", choices=tuple(base.alg.SCENES), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh output directory required")

    checkpoint_path = args.checkpoint.resolve()
    checkpoint = verifier.verify_checkpoint(checkpoint_path)
    reconstructed = mapping(checkpoint.get("reconstructed_state"), "reconstructed")
    scene = args.scene
    if (
        checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or (checkpoint.get("independent_scene_boundary") or {}).get("scene") != scene
    ):
        raise RuntimeError("independent scene checkpoint differs")
    incumbent = mapping(
        mapping(reconstructed.get("incumbents"), "incumbents").get(scene),
        "scene incumbent",
    )
    params = mapping(
        mapping(reconstructed.get("params_by_scene"), "params by scene").get(scene),
        "scene params",
    )
    completed = int(checkpoint.get("completed_outer_attempts") or 0)
    outer = mapping(
        mapping(checkpoint.get("evidence"), "checkpoint evidence")
        .get("outers"),
        "outer evidence",
    ).get(str(completed))
    decision_binding = mapping(
        mapping(outer, "completed outer").get("decisions"), "decisions",
    ).get(scene)
    decision_path = Path(str(mapping(decision_binding, "decision binding").get("path"))).resolve()
    if base.alg.sha(decision_path) != decision_binding.get("sha256"):
        raise RuntimeError("accepted decision binding differs")
    decision = base.alg.load(decision_path)
    matching = [
        row for row in (decision.get("evaluations") or [])
        if row.get("outer_decision", {}).get("accepted") is True
        and math.isclose(
            float(row.get("db_qps") or 0), float(incumbent.get("db_qps") or -1),
            rel_tol=1e-12, abs_tol=1e-12,
        )
        and base.alg.optimizer_params_key(row.get("params") or {})
        == base.alg.optimizer_params_key(params)
    ]
    if decision.get("accepted") is not True or len(matching) != 1:
        raise RuntimeError("checkpoint incumbent accepted measurement differs")
    accepted = matching[0]

    freeze_path = Path(str(checkpoint.get("freeze_path") or "")).resolve()
    if base.alg.sha(freeze_path) != checkpoint.get("freeze_sha256"):
        raise RuntimeError("checkpoint resource freeze differs")
    freeze = base.alg.load(freeze_path)
    worker_ids = sorted(
        str(row["worker_id"])
        for row in (freeze.get("workers") or [])
        if row.get("scene") == scene
    )
    if len(worker_ids) != 2:
        raise RuntimeError("scene two-worker freeze differs")
    measurements = {}
    for worker_id in worker_ids:
        measurements[worker_id] = {
            "task": worker_id,
            "worker_id": worker_id,
            "scene": scene,
            "rank": 0,
            "params": copy.deepcopy(params),
            "db_qps": float(incumbent["db_qps"]),
            "p95_ms": float(incumbent["p95_ms"]),
            "p99_ms": float(incumbent["p99_ms"]),
            "resource_means": copy.deepcopy(incumbent["resource_means"]),
            "hard_gate_passed": True,
            "coverage_complete": True,
            "semantic_noop": True,
            "formal_measurement_performed": False,
            "physical_workload_started": False,
            "checkpoint_incumbent_carry_forward": True,
            "source_accepted_rank": int(accepted.get("rank") or 0),
        }
    document = {
        "schema_version": "mysql_algorithm_c_checkpoint_incumbent_baseline_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "failures": [],
        "measurements": measurements,
        "scene": scene,
        "candidate_count": 0,
        "physical_workload_started": False,
        "formal_measurement_performed": False,
        "checkpoint_incumbent_carry_forward": True,
        "accepted_or_rejected_credit_increment": 0,
        "evidence": {
            "checkpoint": binding(checkpoint_path),
            "accepted_decision": binding(decision_path),
            "resource_freeze": binding(freeze_path),
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": base.alg.sha(Path(__file__).resolve()),
    }
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "checkpoint_incumbent_baseline.json"
    base.alg.atomic(output, document)
    print(json.dumps({
        "status": "PASS",
        "scene": scene,
        "output": str(output),
        "sha256": base.alg.sha(output),
        "incumbent_db_qps": incumbent["db_qps"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
