#!/usr/bin/env python3
"""Focused tests for Memory v139 answer-blind outer-loop controller corrections."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "shell" / "run_bonect_phase2_memory_v112_answer_blind_outer_loop.py"


def load_controller():
    spec = importlib.util.spec_from_file_location("memory_v139_outer_loop", CONTROLLER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_round0_grid_is_exactly_78125_unique_seven_param() -> None:
    mod = load_controller()
    grid = mod.generate_five_point_grid(mod.DEFAULT_RANGES)
    unique = mod.unique_seven_param_candidates(grid)
    assert len(unique) == 78125, len(unique)
    assert all(set(item) == set(mod.COST_PARAMS) for item in unique)
    # Must not be loaded from an old top-k file.
    assert not hasattr(mod, "build_round0_candidates")
    assert not hasattr(mod, "HOLDOUT_TEACHER_SIGNATURES")


def test_replay_worker_pool_selection_supports_gated_four_way_mode() -> None:
    mod = load_controller()
    pool = (
        ROOT
        / "config/generated/bonect_phase2_vm_memory_blind_v112/memory_blind_v112_worker4_pool.json"
    )
    assert mod.load_replay_worker_ids(pool, scenario="memory", max_workers=4) == [
        "bonect-replay-v87-1",
        "bonect-replay-v87-2",
        "bonect-replay-v87-3",
        "bonect-replay-v87-4",
    ]
    assert mod.load_replay_worker_ids(pool, scenario="memory", max_workers=2) == [
        "bonect-replay-v87-1",
        "bonect-replay-v87-2",
    ]


def test_controller_exposes_fail_closed_replay_pool_fallback() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")
    assert '"--fallback-worker-pool-json"' in source
    assert '"--fallback-replay-workers"' in source
    assert "all baseline-only preflight attempts failed" in source
    assert 'state["active_worker_pool_json"]' in source
    assert 'state["active_replay_workers"]' in source


def test_adaptive_budget_12_18_24_executable() -> None:
    mod = load_controller()
    stay = mod.decide_adaptive_replay_budget(
        current_budget=12,
        pairwise_inversion_rate=0.05,
        qps_delta_mae=2.0,
        resource_direction_accuracy=0.9,
        rank_uncertainty=0.05,
        lane_coverage_ratio=0.8,
        unresolved_optimistic_upper_bound=False,
    )
    assert stay["expand"] is False
    assert stay["budget"] == 12

    to18 = mod.decide_adaptive_replay_budget(
        current_budget=12,
        pairwise_inversion_rate=0.4394,
        qps_delta_mae=100.0,
    )
    assert to18["expand"] is True
    assert to18["budget"] == 18

    to24 = mod.decide_adaptive_replay_budget(
        current_budget=18,
        unresolved_optimistic_upper_bound=True,
    )
    assert to24["expand"] is True
    assert to24["budget"] == 24

    sat = mod.decide_adaptive_replay_budget(
        current_budget=24,
        pairwise_inversion_rate=0.5,
    )
    assert sat["expand"] is False
    assert sat["saturated"] is True
    assert sat["budget"] == 24


def test_global_explainable_and_fallback_retention_across_rejected_round() -> None:
    mod = load_controller()
    state = {
        "global_explainable_best": None,
        "fallback_db_qps_best": None,
        "accepted_best": None,
    }
    strong = {
        "global_explainable_best": {
            "candidate_id": "strong_explainable",
            "params": dict(mod.DEFAULT_PARAMS),
            "db_qps_delta_pct": 30.0,
            "explainable": True,
            "discovered_by_current_run": True,
        },
        "fallback_db_qps_best": {
            "candidate_id": "strong_fallback",
            "params": dict(mod.DEFAULT_PARAMS),
            "db_qps_delta_pct": 50.0,
            "explainable": False,
            "discovered_by_current_run": True,
        },
    }
    state = mod.merge_retained_bests(state, strong)
    weaker = {
        "global_explainable_best": {
            "candidate_id": "weaker_explainable",
            "params": dict(mod.DEFAULT_PARAMS),
            "db_qps_delta_pct": 16.0,
            "explainable": True,
            "discovered_by_current_run": True,
        },
        "fallback_db_qps_best": {
            "candidate_id": "weaker_fallback",
            "params": dict(mod.DEFAULT_PARAMS),
            "db_qps_delta_pct": 20.0,
            "explainable": False,
            "discovered_by_current_run": True,
        },
    }
    state = mod.merge_retained_bests(state, weaker)
    assert state["global_explainable_best"]["candidate_id"] == "strong_explainable"
    assert state["fallback_db_qps_best"]["candidate_id"] == "strong_fallback"
    assert float(state["global_explainable_best"]["db_qps_delta_pct"]) == 30.0
    assert float(state["fallback_db_qps_best"]["db_qps_delta_pct"]) == 50.0


def test_force_anchor_rejects_external_known_point() -> None:
    mod = load_controller()
    ranked = [
        {
            "params": dict(mod.DEFAULT_PARAMS),
            "estimated_db_qps_delta_pct": 10.0,
            "score": 1.0,
            "candidate_index": 0,
        }
    ]
    try:
        mod.select_model_ranked_prefix(
            ranked,
            budget=12,
            force_anchor={
                "params": dict(mod.DEFAULT_PARAMS),
                "db_qps_delta_pct": 100.0,
                "discovered_by_current_run": False,
            },
            round_index=1,
        )
        raise AssertionError("expected RuntimeError for external anchor")
    except RuntimeError as exc:
        assert "discovered_by_current_run" in str(exc)


def test_initial_prefix_is_model_top12_without_external_anchor() -> None:
    mod = load_controller()
    ranked = []
    for i in range(20):
        params = dict(mod.DEFAULT_PARAMS)
        params["seq_page_cost"] = 1.0 + i * 0.01
        ranked.append(
            {
                "params": params,
                "estimated_db_qps_delta_pct": 100.0 - i,
                "score": 100.0 - i,
                "candidate_index": i,
            }
        )
    prefix = mod.select_model_ranked_prefix(ranked, budget=12, force_anchor=None, round_index=0)
    assert len(prefix) == 12
    assert all(c["source"] == "fresh_model_rank_current_scan" for c in prefix)
    assert all(c["selection_lane"] == "estimator_model_rank" for c in prefix)


def test_round1_grid_generated_after_binary_shrink() -> None:
    mod = load_controller()
    best = {name: (lo + hi) / 2.0 for name, (lo, hi) in mod.DEFAULT_RANGES.items()}
    best["seq_page_cost"] = 0.0001
    new_ranges = mod.update_ranges_from_best(mod.DEFAULT_RANGES, best, min_width_pct=1.0)
    grid = mod.generate_five_point_grid(new_ranges)
    unique = mod.unique_seven_param_candidates(grid)
    assert len(unique) >= 5
    assert all(set(item) == set(mod.COST_PARAMS) for item in unique)
    # Every shrunk interval must contain the best point.
    for name, (lo, hi) in new_ranges.items():
        assert lo <= best[name] <= hi or abs(best[name] - lo) < 1e-12 or abs(best[name] - hi) < 1e-12


def test_dry_run_manifest_fail_closed_pass() -> None:
    mod = load_controller()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        manifest = mod.build_dry_run_manifest(source_path=CONTROLLER, manifest_path=path)
        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["status"] == "pass", json.dumps(loaded.get("gates"), indent=2)
        assert all(loaded["gates"].values())
        assert loaded["round0_grid"]["generated_unique"] == 78125
        assert loaded["initial_replay_selection"]["budget"] == 12
        assert loaded["initial_replay_selection"]["external_or_known_anchor"] is False
        assert loaded["worker_ownership"]["parallel_replay_workers"] == [
            "bonect-replay-v87-2",
            "bonect-replay-v87-4",
        ]
        assert loaded["physical_metrics"]["nonzero_io_gate"] is True
        assert manifest["status"] == "pass"


def test_source_audit_clean() -> None:
    mod = load_controller()
    audit = mod.audit_source_for_known_answer_coordinates(CONTROLLER)
    assert audit["clean"], audit


def test_scanner_range_json_only_and_shard_options() -> None:
    sys.path.insert(0, str(ROOT / "shell"))
    from bonect_full_grid_estimator_scan import (  # type: ignore
        build_arg_parser,
        expected_candidate_count,
        load_scenario_ranges,
    )

    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "--scenario",
            "memory",
            "--grid-points",
            "5",
            "--shard-count",
            "4",
            "--shard-index",
            "1",
            "--range-json",
            "dummy.json",
        ]
    )
    assert args.shard_count == 4
    assert args.shard_index == 1

    with tempfile.TemporaryDirectory() as tmp:
        range_path = Path(tmp) / "ranges.json"
        payload = {
            "memory": {
                "seq_page_cost": {"low": 0.0001, "high": 100.0, "scale": "log"},
                "random_page_cost": {"low": 0.0001, "high": 20.0, "scale": "log"},
                "cpu_tuple_cost": {"low": 1e-6, "high": 1.0, "scale": "log"},
                "cpu_index_tuple_cost": {"low": 1e-6, "high": 1.0, "scale": "log"},
                "cpu_operator_cost": {"low": 1e-6, "high": 1.0, "scale": "log"},
                "parallel_setup_cost": {"low": 0.0, "high": 10000.0, "scale": "linear"},
                "parallel_tuple_cost": {"low": 1e-6, "high": 10.0, "scale": "log"},
            }
        }
        range_path.write_text(json.dumps(payload), encoding="utf-8")
        ranges, meta = load_scenario_ranges(scenario="memory", bonect_md=None, range_json=range_path)
        assert "range_json_only" in str(meta.get("range_source"))
        assert expected_candidate_count(ranges, 5) == 78125


def test_baseline_only_creates_only_baseline_waves() -> None:
    sys.path.insert(0, str(ROOT / "shell"))
    from bonect_parallel_vm_replay import build_replay_waves  # type: ignore

    workers = [
        {"worker_id": "bonect-replay-v87-2"},
        {"worker_id": "bonect-replay-v87-4"},
    ]
    waves = build_replay_waves(
        candidates=[],
        workers=workers,
        scenario="memory",
        anchor=None,
        baseline_only=True,
    )
    assert len(waves) == 1
    ids = [cand["candidate_id"] for _w, cand in waves[0]]
    assert ids == ["__baseline__bonect-replay-v87-2", "__baseline__bonect-replay-v87-4"]
    assert all(not cid.startswith("__anchor__") for cid in ids)
    # Non-baseline-only still includes anchor + non-anchor candidates when not reusing cache.
    mod = load_controller()
    anchor = {
        "candidate_id": "anchor_point",
        "params": dict(mod.DEFAULT_PARAMS),
    }
    other = {
        "candidate_id": "c1",
        "params": {**mod.DEFAULT_PARAMS, "seq_page_cost": 2.0},
    }
    full = build_replay_waves(
        candidates=[anchor, other],
        workers=workers,
        scenario="memory",
        anchor=anchor,
        cache_reused=False,
        baseline_only=False,
    )
    kinds = []
    for wave in full:
        for _w, cand in wave:
            cid = cand["candidate_id"]
            if cid.startswith("__baseline__"):
                kinds.append("baseline")
            elif cid.startswith("__anchor__"):
                kinds.append("anchor")
            else:
                kinds.append("candidate")
    assert kinds[0] == "baseline"
    assert "anchor" in kinds
    assert "candidate" in kinds
    assert "c1" in [cand["candidate_id"] for wave in full for _w, cand in wave]


def test_round0_scan_refuses_without_current_run_baseline_gate() -> None:
    mod = load_controller()
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        state: dict = {}
        try:
            mod.require_current_run_baseline_gate(run_dir=run_dir, state=state)
            raise AssertionError("expected RuntimeError without baseline gate")
        except RuntimeError as exc:
            assert "refused" in str(exc).lower() or "missing" in str(exc).lower()

        baseline_dir = run_dir / "baseline_preflight"
        baseline_dir.mkdir(parents=True)
        # Failed gate must still refuse.
        (baseline_dir / "baseline_gate.json").write_text(
            json.dumps({"passed": False, "failures": ["baseline_target_not_bottleneck"]}),
            encoding="utf-8",
        )
        state["baseline_preflight_dir"] = str(baseline_dir)
        try:
            mod.require_current_run_baseline_gate(run_dir=run_dir, state=state)
            raise AssertionError("expected RuntimeError on failed gate")
        except RuntimeError as exc:
            assert "failed" in str(exc).lower()

        # Passed gate without calibration still refuses.
        (baseline_dir / "baseline_gate.json").write_text(
            json.dumps({"passed": True, "failures": []}),
            encoding="utf-8",
        )
        try:
            mod.require_current_run_baseline_gate(run_dir=run_dir, state=state)
            raise AssertionError("expected RuntimeError without calibration artifact")
        except RuntimeError as exc:
            assert "calibration" in str(exc).lower() or "missing" in str(exc).lower()

        calib = run_dir / "current_baseline_calibration.json"
        calib.write_text(json.dumps({"actual_resource": {"cpu": 1, "memory": 1, "io": 1}}), encoding="utf-8")
        state["current_baseline_calibration_json"] = str(calib)
        path = mod.require_current_run_baseline_gate(run_dir=run_dir, state=state)
        assert path == calib


def test_current_baseline_calibration_used_by_scan_rescore() -> None:
    mod = load_controller()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        template = root / "template_calibration.json"
        template.write_text(
            json.dumps(
                {
                    "source": "v112_template",
                    "actual_resource": {"cpu": 6.0, "memory": 1.5, "io": 500.0},
                    "actual_utilization_pct": {"cpu": 55.0, "memory": 82.0, "io": 35.0},
                    "estimated_resource_before_outer_correction": {
                        "cpu": 1.0,
                        "memory": 1.5,
                        "io": 4.0,
                    },
                    "scales": {"cpu": 6.0, "memory": 1.0, "io": 125.0},
                    "exempt_resources": ["memory"],
                    "replay_delta_tree": {
                        "enabled": True,
                        "required": False,
                        "source": "v112_forest100",
                        # Basename-only path; builder must resolve against template parent.
                        "model_path": "delta_model.json",
                    },
                    "expected_initial_params": dict(mod.DEFAULT_PARAMS),
                }
            ),
            encoding="utf-8",
        )
        # Place the referenced delta model next to the template (production layout).
        (root / "delta_model.json").write_text(json.dumps({"trees": []}), encoding="utf-8")
        baseline_dir = root / "baseline_preflight"
        baseline_dir.mkdir()
        # Two workers with distinct actuals; average should be used.
        cache = {
            "created_at_epoch": __import__("time").time(),
            "scenario": "memory",
            "controls_level": "baseline_only",
            "baseline_gate": {"passed": True},
            "worker_control_rows": {
                "bonect-replay-v87-2": [
                    {
                        "candidate_id": "__baseline__bonect-replay-v87-2",
                        "status": "ok",
                        "actual_resource": {"cpu": 6.0, "memory": 1.6, "io": 580.0},
                        "actual_utilization_pct": {"cpu": 54.0, "memory": 82.0, "io": 36.0},
                        "db_delta": {"db_qps": 65.0},
                    }
                ],
                "bonect-replay-v87-4": [
                    {
                        "candidate_id": "__baseline__bonect-replay-v87-4",
                        "status": "ok",
                        "actual_resource": {"cpu": 7.0, "memory": 1.8, "io": 620.0},
                        "actual_utilization_pct": {"cpu": 58.0, "memory": 84.0, "io": 40.0},
                        "db_delta": {"db_qps": 66.0},
                    }
                ],
            },
        }
        (baseline_dir / "worker_calibration_cache.json").write_text(json.dumps(cache), encoding="utf-8")
        (baseline_dir / "baseline_gate.json").write_text(
            json.dumps({"passed": True, "failures": []}), encoding="utf-8"
        )
        out = root / "current_baseline_calibration.json"
        payload = mod.build_current_run_baseline_calibration(
            template_path=template,
            baseline_output_dir=baseline_dir,
            output_path=out,
        )
        assert abs(payload["actual_resource"]["cpu"] - 6.5) < 1e-9
        assert abs(payload["actual_resource"]["memory"] - 1.7) < 1e-9
        assert abs(payload["actual_resource"]["io"] - 600.0) < 1e-9
        # Memory exempt keeps scale 1.0; cpu/io recomputed from estimated.
        assert payload["scales"]["memory"] == 1.0
        assert abs(payload["scales"]["cpu"] - 6.5) < 1e-9
        assert abs(payload["scales"]["io"] - 150.0) < 1e-9
        assert payload["replay_delta_tree"]["source"] == "v112_forest100"
        resolved_model = Path(payload["replay_delta_tree"]["model_path"])
        assert resolved_model.is_absolute(), resolved_model
        assert resolved_model.is_file(), resolved_model
        assert payload["current_run_baseline"]["known_parameter_coordinates_loaded"] is False
        assert "teacher_params" not in payload

        # Scan/rescore command builders must reference the current-run calibration path.
        scan_cmd_dir = root / "scan"
        scan_cmd_dir.mkdir()
        # We only verify the command assembly path argument, not launch a scan.
        endpoints = list(mod.DEFAULT_PLANNING_ENDPOINTS)
        # Monkeypatch run_cmd to capture.
        captured = {}

        def fake_run_cmd(cmd, *, cwd, log_path, env=None):
            captured["cmd"] = list(cmd)
            captured["log_path"] = str(log_path)
            return 0

        original = mod.run_cmd
        mod.run_cmd = fake_run_cmd  # type: ignore
        try:
            rc = mod.run_fresh_grid_scan(
                python=sys.executable,
                config_path=root / "cfg.json",
                range_json=root / "ranges.json",
                output_dir=scan_cmd_dir,
                baseline_calibration=out,
                resource_model_dir=None,
                candidate_workers=16,
                run_id="test_scan",
                resume=False,
                planning_endpoints=endpoints,
            )
            assert rc == 0
            assert str(out) in captured["cmd"]
            assert captured["cmd"].count("--pg-endpoint") == 3
            for ep in endpoints:
                assert ep in captured["cmd"]
            assert "192.0.2.13" not in " ".join(captured["cmd"])

            rescore_dir = root / "rescore"
            rescore_dir.mkdir()
            rc2 = mod.run_offline_rescore(
                python=sys.executable,
                candidates_jsonl=scan_cmd_dir / "candidates.jsonl",
                config_path=root / "cfg.json",
                baseline_calibration=out,
                output_dir=rescore_dir,
                top_k=60,
            )
            assert rc2 == 0
            assert str(out) in captured["cmd"]
        finally:
            mod.run_cmd = original  # type: ignore


def test_expansion_12_18_24_replays_only_new_signatures() -> None:
    mod = load_controller()
    ranked = []
    for i in range(30):
        params = dict(mod.DEFAULT_PARAMS)
        params["seq_page_cost"] = 1.0 + i * 0.01
        ranked.append(
            {
                "params": params,
                "estimated_db_qps_delta_pct": 100.0 - i,
                "score": 100.0 - i,
                "candidate_index": i,
            }
        )
    p12 = mod.select_model_ranked_prefix(ranked, budget=12, force_anchor=None, round_index=0)
    sigs = {mod.param_signature(mod.clean_params(c["params"])) for c in p12}
    p18 = mod.select_model_ranked_prefix(ranked, budget=18, force_anchor=None, round_index=0)
    new18 = mod.select_new_prefix_candidates(p18, sigs)
    assert len(new18) == 6
    sigs |= {mod.param_signature(mod.clean_params(c["params"])) for c in new18}
    p24 = mod.select_model_ranked_prefix(ranked, budget=24, force_anchor=None, round_index=0)
    new24 = mod.select_new_prefix_candidates(p24, sigs)
    assert len(new24) == 6
    # Merge by signature keeps unique seven-param rows.
    rows12 = [
        {"candidate_id": f"c{i}", "params": c["params"], "db_delta": {"db_qps": 10.0 + i}}
        for i, c in enumerate(p12)
    ]
    rows_new = [
        {"candidate_id": f"n{i}", "params": c["params"], "db_delta": {"db_qps": 20.0 + i}}
        for i, c in enumerate(new18)
    ]
    merged = mod.merge_replay_rows_by_signature(rows12, rows_new)
    cand_merged = [r for r in merged if not str(r.get("candidate_id")).startswith("__")]
    assert len(cand_merged) == 18


def test_calibration_cache_mismatch_forces_new_baseline() -> None:
    sys.path.insert(0, str(ROOT / "shell"))
    from bonect_parallel_vm_replay import inspect_calibration_cache  # type: ignore
    import time

    with tempfile.TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "cache.json"
        good = {
            "created_at_epoch": time.time(),
            "scenario": "memory",
            "pool_signature": "sig-abc",
            "controls_level": "baseline_only",
            "baseline_gate": {"passed": True},
            "worker_control_rows": {
                "w0": [{"candidate_id": "__baseline__w0", "params": {}}],
            },
        }
        cache_path.write_text(json.dumps(good), encoding="utf-8")
        ok = inspect_calibration_cache(
            str(cache_path), signature="sig-abc", scenario="memory", max_age_s=3600.0
        )
        assert ok["usable"] is True
        assert ok["has_baselines"] is True

        # Signature mismatch forces new baseline.
        bad_sig = inspect_calibration_cache(
            str(cache_path), signature="sig-other", scenario="memory", max_age_s=3600.0
        )
        assert bad_sig["usable"] is False
        assert bad_sig["reason"] == "pool_signature_mismatch"

        # Scenario/profile mismatch.
        bad_scenario = inspect_calibration_cache(
            str(cache_path), signature="sig-abc", scenario="cpu", max_age_s=3600.0
        )
        assert bad_scenario["usable"] is False
        assert bad_scenario["reason"] == "scenario_mismatch"

        # Stale age.
        stale = dict(good)
        stale["created_at_epoch"] = time.time() - 10_000
        cache_path.write_text(json.dumps(stale), encoding="utf-8")
        bad_age = inspect_calibration_cache(
            str(cache_path), signature="sig-abc", scenario="memory", max_age_s=3600.0
        )
        assert bad_age["usable"] is False
        assert bad_age["reason"] == "stale_or_future_timestamp"


def test_three_planning_endpoints_and_no_eighth_param() -> None:
    mod = load_controller()
    endpoints = mod.normalize_planning_endpoints(mod.DEFAULT_PLANNING_ENDPOINTS)
    assert len(endpoints) == 3
    assert "192.0.2.12:25432/hybench_sf10x" in endpoints
    assert "192.0.2.11:25432/hybench_sf10x" in endpoints
    assert "192.0.2.14:25432/hybench_sf10x" in endpoints
    try:
        mod.normalize_planning_endpoints(
            ["192.0.2.12:25432/hybench_sf10x", "192.0.2.13:25432/hybench_sf10x"]
        )
        raise AssertionError("held-out v87-3 endpoint must be refused")
    except RuntimeError as exc:
        assert "192.0.2.13" in str(exc)

    # Exactly seven keys; no eighth parameter allowed.
    try:
        mod.clean_params({**mod.DEFAULT_PARAMS, "shared_buffers": "512MB"})
        raise AssertionError("eighth parameter must be rejected")
    except ValueError:
        pass
    audit = mod.audit_source_for_known_answer_coordinates(CONTROLLER)
    assert audit["clean"]
    assert not audit["seven_param_coordinate_literals"]


def _collect_artifact_paths(node: object, out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"artifact_path", "model_path", "path"} and isinstance(value, str):
                if value.endswith(".joblib") or key == "artifact_path":
                    out.append(value)
            else:
                _collect_artifact_paths(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_artifact_paths(item, out)


def test_default_resource_model_dir_is_complete_v112_set() -> None:
    """Fail closed if DEFAULT_RESOURCE_MODEL_DIR is incomplete (parent dir bug)."""
    mod = load_controller()
    model_dir = Path(mod.DEFAULT_RESOURCE_MODEL_DIR)
    assert model_dir.is_dir(), f"DEFAULT_RESOURCE_MODEL_DIR missing: {model_dir}"
    # Must be the nested complete seed dir, not the parent resource_models/ folder.
    assert model_dir.name == "memory_v112_generic_seed_4508333", model_dir
    assert model_dir.parent.name == "resource_models", model_dir

    required_json = [
        "cpu_resource_models.json",
        "memory_resource_models.json",
        "io_resource_models.json",
    ]
    for name in required_json:
        path = model_dir / name
        assert path.is_file(), f"missing required model JSON: {path}"

    # Parent directory alone must NOT be accepted as a complete set.
    parent = model_dir.parent
    for name in required_json:
        assert not (parent / name).is_file(), (
            f"parent resource_models unexpectedly has {name}; "
            "default should still point at nested complete seed dir"
        )

    referenced: list[str] = []
    for name in required_json:
        payload = json.loads((model_dir / name).read_text(encoding="utf-8"))
        _collect_artifact_paths(payload, referenced)

    joblib_refs = sorted({p for p in referenced if str(p).endswith(".joblib")})
    assert joblib_refs, "expected at least one joblib artifact_path in model JSON"
    missing = []
    for rel in joblib_refs:
        candidate = Path(rel)
        if not candidate.is_absolute():
            candidate = ROOT / rel
        if not candidate.is_file():
            # Also allow paths resolved relative to model_dir.
            alt = model_dir / Path(rel).name
            alt2 = model_dir / "model_artifacts" / Path(rel).name
            if not alt.is_file() and not alt2.is_file():
                missing.append(rel)
    assert not missing, f"missing referenced joblib artifacts: {missing}"
    # Sanity: all three resource JSONs load and artifacts live under the seed dir.
    for rel in joblib_refs:
        assert "memory_v112_generic_seed_4508333" in rel.replace("\\", "/"), rel


def main() -> int:
    tests = [
        test_round0_grid_is_exactly_78125_unique_seven_param,
        test_replay_worker_pool_selection_supports_gated_four_way_mode,
        test_controller_exposes_fail_closed_replay_pool_fallback,
        test_adaptive_budget_12_18_24_executable,
        test_global_explainable_and_fallback_retention_across_rejected_round,
        test_force_anchor_rejects_external_known_point,
        test_initial_prefix_is_model_top12_without_external_anchor,
        test_round1_grid_generated_after_binary_shrink,
        test_dry_run_manifest_fail_closed_pass,
        test_source_audit_clean,
        test_scanner_range_json_only_and_shard_options,
        test_baseline_only_creates_only_baseline_waves,
        test_round0_scan_refuses_without_current_run_baseline_gate,
        test_current_baseline_calibration_used_by_scan_rescore,
        test_expansion_12_18_24_replays_only_new_signatures,
        test_calibration_cache_mismatch_forces_new_baseline,
        test_three_planning_endpoints_and_no_eighth_param,
        test_default_resource_model_dir_is_complete_v112_set,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}", flush=True)
    if failed:
        print(f"{failed} test(s) failed", flush=True)
        return 1
    print(f"all {len(tests)} tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
