#!/usr/bin/env python3
"""Build a fail-closed Algorithm B seed release from a converged Algorithm C.

The release does not waive Algorithm B's fresh runtime preflight or baseline.
It only transfers the audited incumbent and final binary intervals after all
four active MySQL-only scenes have satisfied the <=1% / two-stable-round rule.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpointmod


SCENES = (
    "job_memory",
    "tpch_sf4_memory",
    "hybench_xp_cpu",
    "hybench_xp_io",
)
SCENE_CONTRACTS = {
    "job_memory": {
        "benchmark": "job",
        "target_resource": "memory",
        "formal_workload": "full_113_canonical_sql_exactly_once",
    },
    "tpch_sf4_memory": {
        "benchmark": "tpch_sf4",
        "target_resource": "memory",
        "formal_workload": "q1_q22_exactly_once_frozen_five_lane_schedule",
    },
    "hybench_xp_cpu": {
        "benchmark": "native_hybench_xp",
        "target_resource": "cpu",
        "formal_workload": "native_uniform_iq1_iq7_at_35_25_15_15_7_3",
    },
    "hybench_xp_io": {
        "benchmark": "native_hybench_xp",
        "target_resource": "io",
        "formal_workload": "native_uniform_iq1_iq7_at_35_25_15_15_7_3",
    },
}
SCHEMA = "mysql_algorithm_b_from_algorithm_c_interval_release_v1"
ALGORITHM_B_GRID_SOURCE = (
    ROOT / "scripts/mysql_four_scene_algorithm_b_grid_v1.py"
)
ALGORITHM_B_CONTROLLER_SOURCE = (
    ROOT / "scripts/run_mysql_four_scene_algorithm_b_from_c_v1.py"
)
ALGORITHM_B_LAUNCHER_SOURCE = (
    ROOT / "scripts/launch_mysql_four_scene_algorithm_b_from_c_v1.py"
)
ALGORITHM_B_AUTO_LAUNCH_SOURCE = (
    ROOT / "scripts/wait_and_launch_mysql_algorithm_b_after_c_v1.py"
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _scene_mapping(value: Any, label: str) -> dict[str, Any]:
    result = _mapping(value, label)
    if set(result) != set(SCENES):
        raise RuntimeError(f"{label} four-scene keys differ")
    return result


def _bound_regular(path: Path, label: str) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} regular non-symlink file required: {path}")
    return {"path": str(path), "sha256": _sha(path)}


def _interval(
    *, scene: str, active: list[str], ranges: Mapping[str, Any],
    stable: Mapping[str, Any], params: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    released: dict[str, Any] = {}
    audit: dict[str, Any] = {}
    if not active or len(active) != len(set(active)):
        raise RuntimeError(f"{scene} active parameter list differs")
    for name in active:
        current = _mapping(ranges.get(name), f"{scene} {name} range")
        proven = _mapping(stable.get(name), f"{scene} {name} stable interval")
        lower = float(current.get("lower"))
        upper = float(current.get("upper"))
        stable_lower = float(proven.get("lower"))
        stable_upper = float(proven.get("upper"))
        anchor = float(params.get(name))
        ratio = upper / lower if lower > 0.0 else math.inf
        if (
            not all(math.isfinite(value) for value in (
                lower, upper, stable_lower, stable_upper, anchor,
            ))
            or lower <= 0.0
            or upper < lower
            or stable_lower != lower
            or stable_upper != upper
            or ratio > 1.01
            or not lower <= anchor <= upper
        ):
            raise RuntimeError(
                f"{scene} {name} final <=1% stable interval proof differs"
            )
        released[name] = {
            "lower": lower,
            "upper": upper,
            "transform": "log",
        }
        audit[name] = {
            "upper_lower_ratio": ratio,
            "relative_width": ratio - 1.0,
            "within_one_percent": True,
            "algorithm_c_incumbent_inside_interval": True,
        }
    return released, audit


def build_release(checkpoint_path: Path) -> dict[str, Any]:
    checkpoint_path = checkpoint_path.expanduser().resolve()
    checkpoint = checkpointmod.verify_checkpoint(checkpoint_path)
    reconstructed = _mapping(
        checkpoint.get("reconstructed_state"), "Algorithm C reconstructed state",
    )
    source_state_binding = _mapping(
        _mapping(checkpoint.get("evidence"), "Algorithm C checkpoint evidence")
        .get("source_state"),
        "Algorithm C source state binding",
    )
    source_state_path = Path(str(source_state_binding.get("path") or "")).resolve()
    source_state = _load(source_state_path)
    if (
        source_state_binding.get("sha256") != _sha(source_state_path)
        or source_state.get("status") != "COMPLETE"
        or source_state.get("stage") != "FINISHED"
        or source_state.get("formal_scope") != "mysql_only"
        or source_state.get("cross_engine_comparison_prohibited") is not True
        or source_state.get("formal_evidence_eligible") is not True
        or source_state.get("accepted_state_credit_prohibited") is not False
        or tuple(source_state.get("active_scenes") or ()) != SCENES
    ):
        raise RuntimeError("Algorithm C source is not a completed formal four-scene run")

    source_run_root = Path(str(checkpoint.get("source_run_root") or "")).resolve()
    watchdog_path = source_run_root / "runtime_external_interference_watchdog.jsonl"
    if not watchdog_path.is_file() or watchdog_path.is_symlink():
        raise RuntimeError("Algorithm C runtime interference watchdog evidence is absent")
    try:
        watchdog_rows = [
            json.loads(line)
            for line in watchdog_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Algorithm C runtime watchdog evidence is invalid") from error
    interference_events = {
        "runtime_interference_detected_under_exclusive_wave_lease",
        "cross_project_workload_lease_lost_during_formal_wave",
        "cross_project_workload_lease_deadline_exceeded",
    }
    terminal = watchdog_rows[-1] if watchdog_rows else {}
    terminal_fields = _mapping(
        terminal.get("unit_fields"), "Algorithm C watchdog terminal fields",
    )
    if (
        watchdog_rows[0].get("event") != "watchdog_started"
        or any(row.get("event") in interference_events for row in watchdog_rows)
        or terminal.get("event") != "formal_unit_terminal"
        or terminal.get("interference_detected") is not False
        or terminal_fields.get("Result") != "success"
    ):
        raise RuntimeError("Algorithm C full-wave non-interference proof differs")

    freeze_path = Path(str(checkpoint.get("freeze_path") or "")).resolve()
    freeze = _load(freeze_path)
    if (
        checkpoint.get("freeze_sha256") != _sha(freeze_path)
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or freeze.get("formal_launch_authorized") is not True
        or tuple(freeze.get("active_scenes") or ()) != SCENES
        or len(freeze.get("workers") or []) != 8
        or {str(row.get("scene")) for row in freeze.get("workers") or []}
        != set(SCENES)
    ):
        raise RuntimeError("Algorithm C frozen four-scene resource envelope differs")

    accepted = _scene_mapping(reconstructed.get("accepted_rounds"), "accepted rounds")
    stable_rounds = _scene_mapping(
        reconstructed.get("interval_stable_rounds"), "stable rounds",
    )
    converged = _scene_mapping(reconstructed.get("converged"), "converged")
    active_by_scene = _scene_mapping(
        reconstructed.get("active_by_scene"), "active parameters",
    )
    ranges_by_scene = _scene_mapping(
        reconstructed.get("ranges_by_scene"), "final ranges",
    )
    stable_by_scene = _scene_mapping(
        reconstructed.get("stable_intervals"), "stable intervals",
    )
    params_by_scene = _scene_mapping(
        reconstructed.get("params_by_scene"), "final incumbents",
    )
    incumbents = _scene_mapping(
        reconstructed.get("incumbents"), "final real measurements",
    )

    scene_releases: dict[str, Any] = {}
    for scene in SCENES:
        if (
            int(accepted[scene]) != 10
            or int(stable_rounds[scene]) < 2
            or converged[scene] is not True
        ):
            raise RuntimeError(f"{scene} Algorithm C convergence proof differs")
        active = [str(name) for name in active_by_scene[scene]]
        params = _mapping(params_by_scene[scene], f"{scene} incumbent params")
        intervals, interval_audit = _interval(
            scene=scene,
            active=active,
            ranges=_mapping(ranges_by_scene[scene], f"{scene} ranges"),
            stable=_mapping(stable_by_scene[scene], f"{scene} stable intervals"),
            params=params,
        )
        real = _mapping(incumbents[scene], f"{scene} incumbent measurement")
        resources = _mapping(real.get("resource_means"), f"{scene} resources")
        if (
            not all(
                math.isfinite(float(real.get(name)))
                for name in ("db_qps", "p95_ms", "p99_ms")
            )
            or set(resources) != {"cpu", "memory", "io"}
            or not all(math.isfinite(float(value)) for value in resources.values())
        ):
            raise RuntimeError(f"{scene} incumbent real measurement differs")
        scene_releases[scene] = {
            **SCENE_CONTRACTS[scene],
            "active_parameters": active,
            "algorithm_c_final_incumbent_params": {
                name: float(params[name]) for name in sorted(params)
            },
            "algorithm_b_initial_binary_intervals": intervals,
            "interval_audit": interval_audit,
            "algorithm_c_accepted_rounds": int(accepted[scene]),
            "algorithm_c_consecutive_stable_rounds": int(stable_rounds[scene]),
            "algorithm_c_converged": True,
            "algorithm_c_final_real_measurement": copy.deepcopy(real),
        }

    release = {
        "schema_version": SCHEMA,
        "status": "PREPARED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "active_scenes": list(SCENES),
        "algorithm_c_checkpoint": _bound_regular(
            checkpoint_path, "Algorithm C checkpoint",
        ),
        "algorithm_c_source_state": _bound_regular(
            source_state_path, "Algorithm C source state",
        ),
        "algorithm_c_runtime_watchdog": _bound_regular(
            watchdog_path, "Algorithm C runtime watchdog",
        ),
        "frozen_resource_envelope": _bound_regular(
            freeze_path, "frozen resource envelope",
        ),
        "completed_algorithm_c_outer_attempts": int(
            checkpoint.get("completed_outer_attempts") or 0
        ),
        "scenes": scene_releases,
        "handoff_contract": {
            "binary_interval_source": "algorithm_c_final_stable_intervals",
            "initial_point_source": "algorithm_c_final_real_incumbent",
            "interval_transform": "log",
            "algorithm_c_interval_max_upper_lower_ratio": 1.01,
            "algorithm_c_required_consecutive_stable_rounds": 2,
            "algorithm_b_may_expand_seed_intervals": False,
            "algorithm_b_fresh_runtime_preflight_required": True,
            "algorithm_b_fresh_baseline_required": True,
            "algorithm_b_workload_or_resource_envelope_change_prohibited": True,
            "algorithm_b_launch_authorized_by_this_release_alone": False,
        },
        "algorithm_b_runtime_contract": {
            "method": "outer_binary_inner_estimated_coordinate_grid",
            "candidate_generator": _bound_regular(
                ALGORITHM_B_GRID_SOURCE, "Algorithm B candidate generator",
            ),
            "runtime_controller": _bound_regular(
                ALGORITHM_B_CONTROLLER_SOURCE, "Algorithm B runtime controller",
            ),
            "runtime_launcher": _bound_regular(
                ALGORITHM_B_LAUNCHER_SOURCE, "Algorithm B runtime launcher",
            ),
            "auto_launch_waiter": _bound_regular(
                ALGORITHM_B_AUTO_LAUNCH_SOURCE, "Algorithm B auto-launch waiter",
            ),
            "grid_points_per_coordinate": 15,
            "maximum_inner_passes": 20,
            "inner_no_improvement_patience": 2,
            "combination_dimension_range": [2, 4],
            "combination_values_per_parameter": 3,
            "combination_candidate_cap": 0,
            "fixed_real_top_k": 5,
            "target_accepted_binary_shrinks": 10,
            "maximum_interval_ratio": 1.01,
            "required_consecutive_stable_accepted_rounds": 2,
            "qps_resource_prediction_or_score_role": False,
            "model_only_hard_pruning": False,
            "selected_top5_full_plan_evidence_required": True,
            "selected_top5_frozen_full_workload_replay_required": True,
            "fresh_baseline_before_candidate_replay_required": True,
        },
        "workload_invariants": {
            "job_full_113_exactly_once": True,
            "tpch_sf4_q1_q22_exactly_once_five_lanes": True,
            "job_and_tpch_concurrent": True,
            "native_xp_iq1_iq7_uniform_random": True,
            "native_xp_at_mix_percent": [35, 25, 15, 15, 7, 3],
            "native_xp_xap_xtp_ratio_unchanged": True,
            "measurement_wall_seconds": {"minimum": 600, "maximum": 900},
        },
        "builder_source": _bound_regular(
            Path(__file__).resolve(), "Algorithm B handoff builder",
        ),
    }
    release["release_sha256"] = _canonical_sha(release)
    return release


def verify_release(path: Path) -> dict[str, Any]:
    value = _load(path)
    payload = copy.deepcopy(value)
    expected = str(payload.pop("release_sha256", ""))
    if (
        value.get("schema_version") != SCHEMA
        or value.get("status") != "PREPARED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or tuple(value.get("active_scenes") or ()) != SCENES
        or _canonical_sha(payload) != expected
    ):
        raise RuntimeError("Algorithm B handoff release contract or SHA differs")
    for name in (
        "algorithm_c_checkpoint", "algorithm_c_source_state",
        "algorithm_c_runtime_watchdog", "frozen_resource_envelope",
        "builder_source",
    ):
        binding = _mapping(value.get(name), f"{name} binding")
        path_value = Path(str(binding.get("path") or "")).resolve()
        if (
            not path_value.is_file()
            or path_value.is_symlink()
            or _sha(path_value) != binding.get("sha256")
        ):
            raise RuntimeError(f"Algorithm B handoff source differs: {name}")
    runtime = _mapping(
        value.get("algorithm_b_runtime_contract"),
        "Algorithm B runtime contract",
    )
    generator = _mapping(
        runtime.get("candidate_generator"),
        "Algorithm B candidate generator binding",
    )
    generator_path = Path(str(generator.get("path") or "")).resolve()
    if (
        not generator_path.is_file()
        or generator_path.is_symlink()
        or _sha(generator_path) != generator.get("sha256")
    ):
        raise RuntimeError("Algorithm B candidate generator source differs")
    controller = _mapping(
        runtime.get("runtime_controller"),
        "Algorithm B runtime controller binding",
    )
    controller_path = Path(str(controller.get("path") or "")).resolve()
    if (
        not controller_path.is_file()
        or controller_path.is_symlink()
        or _sha(controller_path) != controller.get("sha256")
    ):
        raise RuntimeError("Algorithm B runtime controller source differs")
    launcher = _mapping(
        runtime.get("runtime_launcher"),
        "Algorithm B runtime launcher binding",
    )
    launcher_path = Path(str(launcher.get("path") or "")).resolve()
    if (
        not launcher_path.is_file()
        or launcher_path.is_symlink()
        or _sha(launcher_path) != launcher.get("sha256")
    ):
        raise RuntimeError("Algorithm B runtime launcher source differs")
    waiter = _mapping(
        runtime.get("auto_launch_waiter"),
        "Algorithm B auto-launch waiter binding",
    )
    waiter_path = Path(str(waiter.get("path") or "")).resolve()
    if (
        not waiter_path.is_file()
        or waiter_path.is_symlink()
        or _sha(waiter_path) != waiter.get("sha256")
    ):
        raise RuntimeError("Algorithm B auto-launch waiter source differs")
    _scene_mapping(value.get("scenes"), "released scenes")
    regenerated = build_release(
        Path(str(_mapping(
            value.get("algorithm_c_checkpoint"),
            "Algorithm C checkpoint binding",
        )["path"])),
    )
    observed_semantics = copy.deepcopy(value)
    expected_semantics = copy.deepcopy(regenerated)
    for document in (observed_semantics, expected_semantics):
        document.pop("created_at", None)
        document.pop("release_sha256", None)
    if observed_semantics != expected_semantics:
        raise RuntimeError(
            "Algorithm B handoff semantics differ from reconstructed Algorithm C"
        )
    return value


def _atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{_sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm-c-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,180}", args.output.name) is None:
        parser.error("unsafe output file name")
    if args.output.exists():
        raise RuntimeError(f"fresh output required: {args.output}")
    release = build_release(args.algorithm_c_checkpoint)
    _atomic(args.output.resolve(), release)
    verify_release(args.output.resolve())
    print(json.dumps({
        "status": "PREPARED",
        "output": str(args.output.resolve()),
        "release_sha256": release["release_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
