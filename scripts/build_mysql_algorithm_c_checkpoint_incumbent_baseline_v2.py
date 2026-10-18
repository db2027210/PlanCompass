#!/usr/bin/env python3
"""Build a semantic incumbent baseline from the latest matching accepted decision.

Unlike v1, this builder supports checkpoints whose most recently completed
outer round was rejected.  It scans the checkpoint's bound decision evidence
and selects the unique accepted measurement matching the carried incumbent.
"""

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

from scripts import build_mysql_algorithm_c_checkpoint_incumbent_baseline_v1 as v1


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--scene", choices=tuple(v1.base.alg.SCENES), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh output directory required")

    checkpoint_path = args.checkpoint.resolve()
    checkpoint = v1.verifier.verify_checkpoint(checkpoint_path)
    reconstructed = _mapping(checkpoint.get("reconstructed_state"), "reconstructed")
    scene = args.scene
    if (
        checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or (checkpoint.get("independent_scene_boundary") or {}).get("scene") != scene
    ):
        raise RuntimeError("independent scene checkpoint differs")
    incumbent = _mapping(
        _mapping(reconstructed.get("incumbents"), "incumbents").get(scene),
        "scene incumbent",
    )
    params = _mapping(
        _mapping(reconstructed.get("params_by_scene"), "params by scene").get(scene),
        "scene params",
    )

    matches: list[tuple[int, Path, dict[str, Any]]] = []
    outers = _mapping(_mapping(checkpoint.get("evidence"), "evidence").get("outers"), "outers")
    for outer_key, raw_outer in outers.items():
        decision_binding = (_mapping(raw_outer, f"outer {outer_key}").get("decisions") or {}).get(scene)
        if not isinstance(decision_binding, Mapping):
            continue
        decision_path = Path(str(decision_binding.get("path") or "")).resolve()
        if (
            not decision_path.is_file()
            or decision_path.is_symlink()
            or v1.base.alg.sha(decision_path) != decision_binding.get("sha256")
        ):
            raise RuntimeError(f"outer {outer_key} decision binding differs")
        decision = v1.base.alg.load(decision_path)
        for row in decision.get("evaluations") or []:
            if (
                row.get("outer_decision", {}).get("accepted") is True
                and math.isclose(
                    float(row.get("db_qps") or 0),
                    float(incumbent.get("db_qps") or -1),
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
                and v1.base.alg.optimizer_params_key(row.get("params") or {})
                == v1.base.alg.optimizer_params_key(params)
            ):
                matches.append((int(outer_key), decision_path, dict(row)))
    if len(matches) != 1:
        raise RuntimeError(
            f"checkpoint incumbent accepted measurement must match exactly once; got {len(matches)}"
        )
    source_outer, decision_path, accepted = matches[0]

    freeze_path = Path(str(checkpoint.get("freeze_path") or "")).resolve()
    if v1.base.alg.sha(freeze_path) != checkpoint.get("freeze_sha256"):
        raise RuntimeError("checkpoint resource freeze differs")
    freeze = v1.base.alg.load(freeze_path)
    worker_ids = sorted(
        str(row["worker_id"])
        for row in (freeze.get("workers") or [])
        if row.get("scene") == scene
    )
    if len(worker_ids) not in (2, 4):
        raise RuntimeError("scene worker freeze differs")
    measurements = {
        worker_id: {
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
            "source_accepted_outer": source_outer,
        }
        for worker_id in worker_ids
    }
    document = {
        "schema_version": "mysql_algorithm_c_checkpoint_incumbent_baseline_v2",
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
        "latest_completed_outer_may_be_rejected": True,
        "evidence": {
            "checkpoint": v1.binding(checkpoint_path),
            "accepted_decision": v1.binding(decision_path),
            "resource_freeze": v1.binding(freeze_path),
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": v1.base.alg.sha(Path(__file__).resolve()),
    }
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "checkpoint_incumbent_baseline.json"
    v1.base.alg.atomic(output, document)
    print(json.dumps({
        "status": "PASS",
        "scene": scene,
        "output": str(output),
        "sha256": v1.base.alg.sha(output),
        "incumbent_db_qps": incumbent["db_qps"],
        "source_accepted_outer": source_outer,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
