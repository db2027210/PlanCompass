#!/usr/bin/env python3
"""Continue TPC-H Algorithm B after the frozen two-worker cardinality failure.

The predecessor campaign completed its baseline and all fifteen calibration
measurements, then failed before the first outer candidate was measured because
the TPC-H successor was wired to the obsolete four-lane scheduler.  This
technical continuation selects the frozen two-worker [1,2], [3,4], [5]
scheduler and reuses only the already verified baseline/calibration evidence.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v9 as two_worker
from scripts import run_mysql_tpch_algorithm_b_client_qps_fresh_v1 as predecessor


CONTINUATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_bc_client_qps_20260830_v1"
    / "algorithm_b_two_worker_continuation_contract.json"
)


def install_runtime() -> Any:
    predecessor.predecessor = two_worker
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_configure_runtime = impl["configure_runtime"]
    original_state_document = impl["state_document"]

    def validate_continuation_contract() -> dict[str, Any]:
        value = alg.load(CONTINUATION_CONTRACT)
        if (
            value.get("schema_version")
            != "mysql_tpch_algorithm_b_two_worker_continuation_v1"
            or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
            or value.get("formal_scope") != "mysql_only"
            or value.get("cross_engine_comparison_prohibited") is not True
            or value.get("completed_calibration_waves_reused") != 15
            or value.get("accepted_or_rejected_outer_credit_reused") is not False
            or value.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        ):
            raise RuntimeError("TPC-H B two-worker continuation contract differs")
        for binding in (value.get("source_bindings") or {}).values():
            if not isinstance(binding, Mapping):
                raise RuntimeError("TPC-H B continuation binding is not an object")
            path = ROOT / str(binding.get("path") or "")
            if (
                not path.is_file() or path.is_symlink()
                or alg.sha(path) != binding.get("sha256")
            ):
                raise RuntimeError(f"TPC-H B continuation binding differs: {path}")
        return value

    def configure_runtime() -> Mapping[str, Any]:
        configured = original_configure_runtime()
        validate_continuation_contract()
        return configured

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        document.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "tpch_b_two_worker_continuation_contract_path": str(
                CONTINUATION_CONTRACT
            ),
            "tpch_b_two_worker_continuation_contract_sha256": alg.sha(
                CONTINUATION_CONTRACT
            ),
            "formal_candidate_batches": [[1, 2], [3, 4], [5]],
            "physical_replay_parallelism": 2,
        })
        document.setdefault("technical_continuation", {}).update(copy.deepcopy({
            "kind": "post_cardinality_failure_two_worker_boundary_resume_v1",
            "completed_calibration_waves_reused": 15,
            "accepted_or_rejected_outer_credit_reused": False,
            "partial_outer_candidate_measurements_reused": False,
        }))
        return document

    impl["configure_runtime"] = configure_runtime
    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
