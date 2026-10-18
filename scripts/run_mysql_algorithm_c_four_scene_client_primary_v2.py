#!/usr/bin/env python3
"""Run the v91 fully bound client-primary Algorithm-C release."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v1 as predecessor


CONTRACT_ROOT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v91"
)


def validate_contract_v91() -> dict[str, Any]:
    alg = predecessor.predecessor.predecessor.alg
    parent = alg.load(CONTRACT_ROOT / "algorithm_c_async_contract.json")
    optimization = alg.load(CONTRACT_ROOT / "outer_runtime_optimization.json")
    successor = alg.load(CONTRACT_ROOT / "global_monotonic_incumbent_contract.json")
    if (
        parent.get("release_version") != "v91-client-primary"
        or parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("outer_acceptance_policy")
        != predecessor.predecessor.predecessor.qps_policy.policy_contract()
        or parent.get("outer_rejection_cycle_policy")
        != predecessor.predecessor.predecessor.rejection_cycle.policy_contract()
        or parent.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or parent.get("physical_replay_parallelism_per_scene") != 2
        or parent.get("maximum_concurrent_mysql_workers") != 8
        or parent.get("four_scene_wave_barrier") is not False
        or parent.get("sibling_scene_wait") is not False
        or parent.get("lease_mode") != "none"
        or parent.get("post_search_stability_confirmation") is not False
        or optimization.get("release_version") != "v91-client-primary"
        or successor.get("release_version") != "v91-client-primary"
        or successor.get("parent_contract", {}).get("sha256")
        != alg.sha(CONTRACT_ROOT / "algorithm_c_async_contract.json")
        or successor.get("xp_outer_primary_metric")
        != "native_hybench_xp_client_xap_qps"
    ):
        raise RuntimeError("Algorithm C v91 client-primary contract differs")
    for document in (parent, successor):
        bindings = document.get("source_bindings")
        if not isinstance(bindings, Mapping):
            raise RuntimeError("Algorithm C v91 source bindings absent")
        for name, raw in bindings.items():
            if not isinstance(raw, Mapping):
                raise RuntimeError(f"Algorithm C v91 binding differs: {name}")
            path = (ROOT / str(raw.get("path") or "")).resolve()
            if (
                not path.is_file() or path.is_symlink()
                or alg.sha(path) != str(raw.get("sha256") or "")
            ):
                raise RuntimeError(f"Algorithm C v91 source differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": successor}


def main() -> int:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
    predecessor.OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
    predecessor.CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"
    predecessor.validate_contract = validate_contract_v91
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
