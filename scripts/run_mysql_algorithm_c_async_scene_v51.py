#!/usr/bin/env python3
"""Run asynchronous C without cached-incumbent prephysical preemption."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v50 as v50


runtime = v50.runtime
alg = runtime.alg
ASYNC_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260820_v39"
    / "algorithm_c_async_contract.json"
)
_ORIGINAL_EVALUATE_BATCH = runtime.pg_outer.evaluate_batch


def evaluate_batch_without_cached_incumbent_preemption(
    rows: Sequence[Mapping[str, Any]],
    reference: Mapping[str, Any],
    *, canonical_baseline: Mapping[str, Any] | None = None,
    target: str,
):
    """Do not let a cached-only wave suppress the fresh physical Top-5.

    Cached rows remain eligible when evaluated together with physical rows.
    The guard applies only to the prephysical call whose complete input is
    marked as cached replay evidence.
    """
    selected, valid = _ORIGINAL_EVALUATE_BATCH(
        rows, reference, canonical_baseline=canonical_baseline, target=target,
    )
    if rows and all(
        row.get("cached_valid_replay_redecision") is True for row in rows
    ):
        return None, valid
    return selected, valid


def validate_contract() -> dict[str, Any]:
    if not ASYNC_CONTRACT.is_file() or ASYNC_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed asynchronous C v51 contract absent: {ASYNC_CONTRACT}")
    contract = alg.load(ASYNC_CONTRACT)
    recovery = contract.get("cached_incumbent_preemption_successor") or {}
    if (
        contract.get("release_version") != "v51"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("scene_may_advance_immediately") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("post_search_stability_confirmation") is not False
        or recovery.get("cached_only_prephysical_acceptance_allowed") is not False
        or recovery.get("fresh_physical_top5_preserved") is not True
        or recovery.get("accepted_and_rejected_credit_changed") is not False
        or recovery.get("postgresql_outer_flow_changed") is not False
    ):
        raise RuntimeError("v51 Algorithm C asynchronous contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v51 Algorithm C source binding differs: {path}")
    return contract


def install() -> None:
    v50.ASYNC_CONTRACT = ASYNC_CONTRACT
    v50.install()
    runtime.pg_outer.evaluate_batch = (
        evaluate_batch_without_cached_incumbent_preemption
    )
    original_state_document = v50.v49.v28.ORIGINAL_V27_STATE_DOCUMENT

    def successor_state_document(**kwargs: Any) -> dict[str, Any]:
        document = copy.deepcopy(original_state_document(**kwargs))
        document["cached_incumbent_preemption_release"] = {
            "release_version": "v51",
            "source_path": str(Path(__file__).resolve()),
            "source_sha256": alg.sha(Path(__file__).resolve()),
            "cached_only_prephysical_acceptance_allowed": False,
            "fresh_physical_top5_preserved": True,
            "accepted_and_rejected_credit_changed": False,
        }
        return document

    v50.v49.v28.ORIGINAL_V27_STATE_DOCUMENT = successor_state_document


def main() -> int:
    validate_contract()
    v50.v49.validate_v65_wave_coordination_contract()
    install()
    return v50.v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())
