#!/usr/bin/env python3
"""Resume C with durable post-target confirmation state across wave reloads.

The coordinated batch helper reloads ``run_state.json`` after every lease.
Plain JSON decoding erased v20's ``ConfirmationAcceptedRounds`` type and made
10/10 + stable 1/2 look terminal before candidate ranking.  This release
rehydrates that type on every run-state load and persists its bounded accepted
confirmation counters explicitly.  No scoring, workload, Top-5, interval, or
acceptance rule changes.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v20 as v20
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v22 as v22


alg = v22.alg
ORIGINAL_LOAD = alg.load
ORIGINAL_V20_ATOMIC = v20.atomic
STATE_SCHEMA = "mysql_four_scene_algorithm_c_boundary_resume_state_v2"
COUNTER_FIELD = "post_target_stability_confirmation_counts"


def _rehydrate(document: dict[str, Any]) -> dict[str, Any]:
    global v20
    release = document.get("post_target_stability_confirmation_release") or {}
    if (
        document.get("schema_version") != STATE_SCHEMA
        or release.get("release_version") != "v20"
        or not isinstance(document.get("accepted_rounds"), Mapping)
    ):
        return document
    accepted = v20.ConfirmationAcceptedRounds(document["accepted_rounds"])
    persisted = document.get(COUNTER_FIELD) or {}
    if set(persisted) not in ({}, set(accepted)):
        raise RuntimeError("post-target confirmation counter scene set differs")
    accepted.confirmation_acceptances = {
        scene: int(persisted.get(scene, 0)) for scene in accepted
    }
    if any(
        value < 0 or value > v20.MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS
        for value in accepted.confirmation_acceptances.values()
    ):
        raise RuntimeError("post-target confirmation counter exceeds bound")
    document["accepted_rounds"] = accepted
    v20.ACTIVE_ACCEPTED_ROUNDS = accepted
    return document


def load(path: Path) -> dict[str, Any]:
    return _rehydrate(ORIGINAL_LOAD(path))


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    document: Mapping[str, Any] = value
    accepted = value.get("accepted_rounds")
    if (
        value.get("schema_version") == STATE_SCHEMA
        and isinstance(accepted, v20.ConfirmationAcceptedRounds)
    ):
        if isinstance(value, dict):
            value[COUNTER_FIELD] = copy.deepcopy(accepted.confirmation_acceptances)
            value["durable_post_target_confirmation_release"] = {
                "release_version": "v23",
                "source_path": str(Path(__file__).resolve()),
                "source_sha256": alg.sha(Path(__file__).resolve()),
                "json_reload_rehydrates_confirmation_type": True,
                "accepted_round_credit_remains_capped_at_10": True,
                "maximum_accepted_confirmations": (
                    v20.MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS
                ),
                "workload_scoring_top5_binary_shrink_and_convergence_changed": False,
            }
            document = value
        else:
            amended = copy.deepcopy(dict(value))
            amended[COUNTER_FIELD] = copy.deepcopy(accepted.confirmation_acceptances)
            document = amended
    ORIGINAL_V20_ATOMIC(path, document)


def validate_release() -> None:
    sample = {
        "schema_version": STATE_SCHEMA,
        "post_target_stability_confirmation_release": {"release_version": "v20"},
        "accepted_rounds": {
            "job_memory": 10, "tpch_sf4_memory": 10,
            "hybench_xp_cpu": 9, "hybench_xp_io": 8,
        },
        COUNTER_FIELD: {
            "job_memory": 0, "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 0, "hybench_xp_io": 0,
        },
    }
    hydrated = _rehydrate(copy.deepcopy(sample))
    accepted = hydrated["accepted_rounds"]
    if (
        accepted["job_memory"] >= 10
        or accepted["hybench_xp_cpu"] >= 10
        or int(accepted["job_memory"]) != 10
    ):
        raise RuntimeError("post-target confirmation open-state test failed")
    accepted["job_memory"] += 1
    if (
        int(accepted["job_memory"]) != 10
        or accepted.confirmation_acceptances["job_memory"] != 1
        or accepted["job_memory"] >= 10
    ):
        raise RuntimeError("first bounded confirmation test failed")
    accepted["job_memory"] += 1
    if (
        int(accepted["job_memory"]) != 10
        or accepted.confirmation_acceptances["job_memory"] != 2
        or not (accepted["job_memory"] >= 10)
    ):
        raise RuntimeError("bounded confirmation terminal test failed")


def main() -> int:
    validate_release()
    alg.load = load
    v20.atomic = atomic
    return v22.main()


if __name__ == "__main__":
    raise SystemExit(main())
