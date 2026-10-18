#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from find_hybenchxp_plan_sensitive_sql import (
    DEFAULTS,
    FINAL_AUDIT_PROFILES,
    build_capture_candidates,
    build_profiles,
    add_axis_sweep_profiles,
    load_profiles_manifest,
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-vm-plan-probe-") as temp_dir:
        capture = Path(temp_dir) / "capture.json"
        capture.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "queryid": "1",
                            "query": "select * from transfer where id = $1",
                            "plan_hash": "abc",
                        },
                        {
                            "queryid": "2",
                            "query": " SELECT   * from transfer where id = $1; ",
                            "plan_hash": "def",
                        },
                        {
                            "queryid": "3",
                            "query": "select 1",
                            "plan_hash": None,
                            "error": "capture failed",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        candidates = build_capture_candidates(capture)

    assert len(candidates) == 1, candidates
    assert candidates[0]["source"] == "vm_plan_capture", candidates
    profiles = build_profiles(include_bounds=True, include_final_audit=True)
    required_profiles = {"baseline", *FINAL_AUDIT_PROFILES}
    assert required_profiles.issubset(profiles), profiles.keys()
    for name, params in profiles.items():
        assert set(params) == set(DEFAULTS), (name, params)
    assert profiles["final_audit_cpu"]["seq_page_cost"] == 75.000025
    assert profiles["final_audit_memory_io"]["parallel_tuple_cost"] == 10.0
    assert profiles["final_audit_none"]["parallel_setup_cost"] == 8750.0

    with tempfile.TemporaryDirectory(prefix="phase2-vm-plan-profiles-") as temp_dir:
        manifest = Path(temp_dir) / "candidates.json"
        manifest.write_text(
            json.dumps(
                [
                    {
                        "candidate_id": "page_boundary",
                        "params": {**DEFAULTS, "seq_page_cost": 10.0, "random_page_cost": 5.0},
                    }
                ]
            ),
            encoding="utf-8",
        )
        loaded = load_profiles_manifest(manifest)
        assert set(loaded) == {"baseline", "page_boundary"}, loaded
        assert set(loaded["page_boundary"]) == set(DEFAULTS), loaded
        assert loaded["page_boundary"]["seq_page_cost"] == 10.0, loaded

        swept = add_axis_sweep_profiles(
            loaded,
            base_name="page_boundary",
            sweep_specs=["cpu_operator_cost=0.001,0.01", "parallel_tuple_cost=1,5"],
        )
        assert len(swept) == len(loaded) + 4, swept
        assert swept["sweep_cpu_operator_cost_0p001"]["seq_page_cost"] == 10.0, swept
        assert swept["sweep_parallel_tuple_cost_5"]["parallel_tuple_cost"] == 5.0, swept

        invalid = Path(temp_dir) / "invalid.json"
        invalid.write_text(json.dumps({"bad": {**DEFAULTS, "eighth": 1}}), encoding="utf-8")
        try:
            load_profiles_manifest(invalid)
        except ValueError as exc:
            assert "exactly seven" in str(exc), exc
        else:
            raise AssertionError("eighth cost parameter must be rejected")
    print("PASS VM plan-boundary manifest uses capture SQL and seven-cost profiles", flush=True)


if __name__ == "__main__":
    main()
