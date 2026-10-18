#!/usr/bin/env python3
"""Rebind the TPC-H C structural checkpoint to the sealed disk2 worker pair.

The successor is valid only with ``--fresh-search``: no old incumbent,
candidate measurement, accepted round, or rejected round may be credited.
The checkpoint supplies the verified Algorithm-C state schema and evidence
lineage; the new campaign measures a fresh baseline on the new worker pair.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as verifier
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


OLD = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3"
    / "four_scene_effective_resource_successor.json"
)
NEW = (
    ROOT / "config/formal/frozen_mysql_tpch_bc_disk2_two_worker_20260830_v1"
    / "four_scene_effective_resource_successor.json"
)
SCENE = "tpch_sf4_memory"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fresh-search", action="store_true", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("safe output directory required")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh output required")

    checkpoint_path = args.checkpoint.resolve()
    document = verifier.verify_checkpoint(checkpoint_path)
    old, new = alg.load(OLD), alg.load(NEW)
    new_rows = [row for row in new["workers"] if row["scene"] == SCENE]
    if (
        Path(str(document.get("freeze_path") or "")).resolve() != OLD.resolve()
        or document.get("freeze_sha256") != alg.sha(OLD)
        or old.get("schema_version")
        != "mysql_four_scene_cpu_a_slots_four_worker_successor_v3"
        or new.get("schema_version")
        != "mysql_tpch_bc_disk2_two_worker_successor_v1"
        or new.get("formal_scope") != "mysql_only"
        or new.get("cross_engine_comparison_prohibited") is not True
        or {str(row["worker_id"]) for row in new_rows}
        != {"tpch_w1", "tpch_w2"}
        or any(
            not str(row.get("runtime_root") or "").startswith("/disk2/")
            for row in new_rows
        )
    ):
        raise RuntimeError("TPC-H disk2 checkpoint transition differs")

    document["freeze_path"] = str(NEW)
    document["freeze_sha256"] = alg.sha(NEW)
    document["builder_source_path"] = str(Path(__file__).resolve())
    document["builder_source_sha256"] = alg.sha(Path(__file__).resolve())
    document["tpch_disk2_fresh_search_checkpoint_rebind"] = {
        "schema_version": "mysql_algorithm_c_tpch_disk2_fresh_search_rebind_v1",
        "status": "PASS",
        "scene": SCENE,
        "source_checkpoint": {
            "path": str(checkpoint_path),
            "sha256": alg.sha(checkpoint_path),
        },
        "predecessor_freeze": {"path": str(OLD), "sha256": alg.sha(OLD)},
        "successor_freeze": {"path": str(NEW), "sha256": alg.sha(NEW)},
        "target_worker_count": 2,
        "target_worker_ids": ["tpch_w1", "tpch_w2"],
        "target_storage_mounts": ["/disk2"],
        "fresh_search_required": True,
        "fresh_baseline_required": True,
        "old_incumbent_reuse_prohibited": True,
        "old_candidate_measurement_reuse_prohibited": True,
        "old_accepted_or_rejected_round_credit_prohibited": True,
        "algorithm_a_parameter_or_measurement_read": False,
        "structural_checkpoint_only": True,
    }
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = (
        verifier.predecessor.predecessor._canonical_sha256(payload)
    )
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "prepared_binding.json"
    alg.atomic(output, document)
    verifier.verify_checkpoint(output)
    print(json.dumps({
        "status": "PREPARED",
        "scene": SCENE,
        "checkpoint": str(output),
        "freeze_sha256": document["freeze_sha256"],
        "fresh_search_required": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
