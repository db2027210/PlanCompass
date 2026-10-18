#!/usr/bin/env python3
"""Regression checks for external Bonect forced replay anchor seeds."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import run_pg_benchmark_bottleneck_matrix as runner


def _base_params() -> dict:
    return {
        "seq_page_cost": 0.8,
        "random_page_cost": 3.6363636363636367,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.003,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_external_seed_priority_and_dedup() -> None:
    old_env = os.environ.get("BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            seed_path = Path(tmp) / "seeds.json"
            params = _base_params()
            _write_json(
                seed_path,
                [
                    {
                        "name": "external_priority_anchor",
                        "optimized_params": params,
                        "db_qps_delta_pct": 20.0,
                    },
                    {
                        "name": "external_duplicate_anchor",
                        "optimized_params": dict(params),
                        "db_qps_delta_pct": 19.0,
                    },
                ],
            )
            os.environ["BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON"] = str(seed_path)
            records = runner.bonect_forced_replay_anchors("none", "hybench_xp")
            assert records, "expected external records"
            assert records[0]["name"] == "external_priority_anchor", records[0]
            names = [item.get("name") for item in records]
            assert "external_duplicate_anchor" not in names, names
            assert set(records[0]["optimized_params"]) == runner.COST_PARAM_SET
    finally:
        if old_env is None:
            os.environ.pop("BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON", None)
        else:
            os.environ["BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON"] = old_env


def test_external_seed_rejects_extra_parameter() -> None:
    old_env = os.environ.get("BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            seed_path = Path(tmp) / "bad_seeds.json"
            params = _base_params()
            params["work_mem"] = 4096
            _write_json(
                seed_path,
                [{"name": "bad_extra_param", "optimized_params": params}],
            )
            os.environ["BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON"] = str(seed_path)
            try:
                runner.bonect_forced_replay_anchors("none", "hybench_xp")
            except RuntimeError as exc:
                assert "exactly the 7 cost params" in str(exc), str(exc)
            else:
                raise AssertionError("expected RuntimeError for extra parameter")
    finally:
        if old_env is None:
            os.environ.pop("BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON", None)
        else:
            os.environ["BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON"] = old_env


def test_builtin_none_seeds_use_paired_v59_dbqps_fallback() -> None:
    old_env = os.environ.pop("BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON", None)
    try:
        records = runner.bonect_forced_replay_anchors("none", "hybench_xp")
        by_name = {str(item.get("name")): item for item in records}
        name = "phase2_none_v59_cand24_dbqps_fallback_20260710_055902"
        assert name in by_name, sorted(by_name)
        record = by_name[name]
        assert record["db_qps_delta_pct"] == 6.01432, record
        assert set(record["optimized_params"]) == runner.COST_PARAM_SET, record
        assert record["primary_target_status"]["target_db_qps_delta_pct"] == 15.0, record
        assert record["primary_target_status"]["db_qps_target_met"] is False, record
    finally:
        if old_env is not None:
            os.environ["BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON"] = old_env


def main() -> None:
    test_external_seed_priority_and_dedup()
    test_external_seed_rejects_extra_parameter()
    test_builtin_none_seeds_use_paired_v59_dbqps_fallback()
    print("PASS forced replay anchor seed JSON tests", flush=True)


if __name__ == "__main__":
    main()
