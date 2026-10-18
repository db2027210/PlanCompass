#!/usr/bin/env python3
"""Tests for Algorithm C worker-balanced archive racing."""

from __future__ import annotations

import inspect
import math

from algorithm_c_archive_racing import (
    archive_race_identities,
    balanced_race_params,
    robust_best_feasible,
)


def observation(label: str, worker: str, qps: float):
    return {"label": label, "worker_key": worker, "qps": qps}


def main() -> int:
    archive = {
        "fast_noisy": {
            "params": {"seq_page_cost": 0.8},
            "observations": [observation("f1", "w1", 140.0)],
        },
        "balanced": {
            "params": {"seq_page_cost": 1.1},
            "observations": [
                observation("b1", "w1", 110.0),
                observation("b2", "w2", 108.0),
            ],
        },
        "slow": {
            "params": {"seq_page_cost": 1.4},
            "observations": [observation("s1", "w2", 90.0)],
        },
    }
    identities = archive_race_identities(archive, physical_slot_budget=4)
    assert identities == ["fast_noisy", "balanced"]
    waves = balanced_race_params(archive, identities)
    assert len(waves) == 2
    assert all(len(wave) == 2 and wave[0] == wave[1] for wave in waves)
    waves[0][0]["seq_page_cost"] = 99.0
    assert waves[0][1]["seq_page_cost"] == 0.8
    assert archive["fast_noisy"]["params"]["seq_page_cost"] == 0.8

    selected = robust_best_feasible(
        archive,
        expected_workers=("w1", "w2"),
        pooled_sigma_log=0.02,
    )
    assert selected is not None
    assert selected["identity"] == "balanced"
    assert selected["worker_coverage"] == ["w1", "w2"]
    assert selected["observation_count"] == 2
    assert math.isclose(selected["observed_qps_geomean"], math.sqrt(110.0 * 108.0))

    # Once the high one-shot observation is checked on both workers and proves
    # repeatable, the conservative selector is allowed to prefer it.
    archive["fast_noisy"]["observations"].append(observation("f2", "w2", 138.0))
    selected = robust_best_feasible(
        archive,
        expected_workers=("w1", "w2"),
        pooled_sigma_log=0.02,
    )
    assert selected is not None
    assert selected["identity"] == "fast_noisy"

    assert archive_race_identities(archive, physical_slot_budget=1) == []
    assert robust_best_feasible(
        {"single": archive["slow"]},
        expected_workers=("w1", "w2"),
        pooled_sigma_log=0.02,
    ) is None

    source = "\n".join(
        inspect.getsource(function)
        for function in (archive_race_identities, balanced_race_params, robust_best_feasible)
    ).lower()
    for forbidden in (
        "job_memory",
        "tpch_memory",
        "xp_cpu",
        "xp_io",
        "algorithm_a",
        "algorithm_b",
        "champion_params",
    ):
        assert forbidden not in source

    print("PASS: Algorithm C worker-balanced archive racing", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
