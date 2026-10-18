#!/usr/bin/env python3
"""Run two serial, state-neutral TPC-H current-epoch anchor observations.

The source formal state is immutable input.  Exactly two workload slots are
reserved in one diagnostic batch, PostgreSQL baseline parameters are restored
after each slot, and a new sealed accepted=0 anchor state is emitted only when
both observations are valid and stable.  This program never updates accepted,
incumbent, range, or gradient state in place.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
from types import ModuleType
from typing import Any, Dict, Mapping


RESOURCES = ("cpu", "io", "memory")
PHYSICAL_CALL_CAP = 2


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def verify(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise RuntimeError(f"{label} SHA256 mismatch: {path}")


def import_runner(path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "tpch_v38_anchor_runner_dependency", path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import runner: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def finite_resource(value: Mapping[str, Any]) -> Dict[str, float]:
    result = {name: float(value[name]) for name in RESOURCES}
    if any(not math.isfinite(number) or number <= 0.0 for number in result.values()):
        raise RuntimeError(f"non-positive or non-finite resource: {result}")
    return result


def read_accepted(path: Path) -> int:
    value = int(path.read_text(encoding="utf-8").strip())
    if value != 0:
        raise RuntimeError(f"fresh accepted counter must remain zero: {value}")
    return value


def resource_factor(observations: list[Mapping[str, Any]]) -> float:
    return max(
        max(float(row["actual_resource"][name]) for row in observations)
        / min(float(row["actual_resource"][name]) for row in observations)
        for name in RESOURCES
    )


def build_anchor_state(
    source: Mapping[str, Any],
    params: Mapping[str, Any],
    observations: list[Mapping[str, Any]],
    provenance: Mapping[str, Any],
    maximum_score_span: float,
    maximum_resource_factor: float,
) -> Dict[str, Any]:
    if len(observations) != PHYSICAL_CALL_CAP:
        raise RuntimeError("anchor requires exactly two observations")
    if any(
        row.get("baseline_restored_after_observation") is not True
        for row in observations
    ):
        raise RuntimeError("anchor observations lack per-call baseline restoration")
    scores = [float(row["actual_score"]) for row in observations]
    score_span = max(scores) - min(scores)
    observed_resource_factor = resource_factor(observations)
    if score_span > maximum_score_span + 1e-12:
        raise RuntimeError(
            f"anchor score span is unstable: {score_span} > {maximum_score_span}"
        )
    if observed_resource_factor > maximum_resource_factor + 1e-12:
        raise RuntimeError(
            "anchor resource factor is unstable: "
            f"{observed_resource_factor} > {maximum_resource_factor}"
        )
    # The formal runner recomputes incumbent_score from incumbent_resource and
    # requires exact agreement.  Bind both to one real observation instead of
    # combining componentwise medians through a nonlinear utility function.
    # The lower-scoring observation is a conservative current-epoch anchor.
    incumbent_observation = min(
        observations,
        key=lambda row: (float(row["actual_score"]), int(row["ordinal"])),
    )
    incumbent_resource = finite_resource(
        incumbent_observation["actual_resource"]
    )
    incumbent_score = float(incumbent_observation["actual_score"])
    result = copy.deepcopy(dict(source))
    result.update(
        {
            "schema_version": "tpch_exact_plan_regime_formal_state_v1",
            "status": "complete",
            "accepted_count": 0,
            "batch_added_accepted_count": 0,
            "physical_call_count": 0,
            "valid_replay_count": 0,
            "technical_failure_count": 0,
            "baseline_restored": True,
            "incumbent_params": copy.deepcopy(dict(params)),
            "incumbent_resource": incumbent_resource,
            "incumbent_score": incumbent_score,
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
            "qps_used_for_inner_scoring": False,
            "current_epoch_anchor_revalidation": {
                "contract": "tpch_current_epoch_anchor_revalidation_v1",
                "state_neutral": True,
                "baseline_restored": True,
                "observation_count": PHYSICAL_CALL_CAP,
                "diagnostic_physical_call_count": PHYSICAL_CALL_CAP,
                "diagnostic_valid_replay_count": PHYSICAL_CALL_CAP,
                "technical_failure_count": 0,
                "actual_score_values": scores,
                "actual_score_median": statistics.median(scores),
                "actual_score_span": score_span,
                "maximum_score_span_allowed": maximum_score_span,
                "maximum_resource_factor": observed_resource_factor,
                "maximum_resource_factor_allowed": maximum_resource_factor,
                "candidate_params": copy.deepcopy(dict(params)),
                "anchor_score_policy": "lower_observed_score_exact_resource_v1",
                "anchor_observation_ordinal": int(
                    incumbent_observation["ordinal"]
                ),
                "anchor_resource": copy.deepcopy(incumbent_resource),
                "anchor_score": incumbent_score,
                "observations": copy.deepcopy(observations),
                "accepted_count_before": 0,
                "accepted_count_after": 0,
                "formal_source_mutated": False,
                "qps_used_for_inner_scoring": False,
                **copy.deepcopy(dict(provenance)),
            },
        }
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-formal-state", type=Path, required=True)
    parser.add_argument("--source-formal-state-sha256", required=True)
    parser.add_argument("--accepted-counter", type=Path, required=True)
    parser.add_argument("--runner-module", type=Path, required=True)
    parser.add_argument("--runner-module-sha256", required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--workload-sha256", required=True)
    parser.add_argument("--workload-driver", type=Path, required=True)
    parser.add_argument("--workload-driver-sha256", required=True)
    parser.add_argument(
        "--runtime-dependency",
        action="append",
        default=[],
        metavar="PATH::SHA256",
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--anchor-mode", choices=("default", "incumbent"), default="default"
    )
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--maximum-score-span", type=float, default=0.05)
    parser.add_argument("--maximum-resource-factor", type=float, default=1.5)
    parser.add_argument("--armed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.armed:
        raise RuntimeError("fail closed: --armed is required")
    if args.output.exists():
        raise FileExistsError(args.output)
    if not (60 <= args.timeout_seconds <= 1800):
        raise RuntimeError("timeout must be in [60, 1800]")
    if not (
        0.0 < args.maximum_score_span <= 0.05
        and 1.0 <= args.maximum_resource_factor <= 1.5
    ):
        raise RuntimeError("anchor stability thresholds may not be relaxed")
    pinned = (
        (args.source_formal_state, args.source_formal_state_sha256, "formal state"),
        (args.runner_module, args.runner_module_sha256, "runner module"),
        (args.workload, args.workload_sha256, "workload"),
        (args.workload_driver, args.workload_driver_sha256, "workload driver"),
        (args.config, args.config_sha256, "config"),
    )
    for path, expected, label in pinned:
        verify(path, expected, label)
    runtime_dependencies = []
    for binding in args.runtime_dependency:
        raw_path, separator, expected = binding.rpartition("::")
        if not separator:
            raise RuntimeError("runtime dependency must use PATH::SHA256")
        path = Path(raw_path).resolve()
        verify(path, expected, f"runtime dependency {path.name}")
        runtime_dependencies.append({"path": str(path), "sha256": expected})
    if len(runtime_dependencies) < 3:
        raise RuntimeError("at least three sealed runtime dependencies required")
    read_accepted(args.accepted_counter)
    source_sha_before = sha256(args.source_formal_state)
    source = load(args.source_formal_state)
    if not (
        source.get("status") == "complete"
        and source.get("baseline_restored") is True
        and int(source.get("accepted_count", -1)) == 0
        and isinstance(source.get("range_state"), Mapping)
        and len(source["range_state"]) == 7
    ):
        raise RuntimeError("source fresh0 state is not a clean range authority")

    runner = import_runner(args.runner_module)
    if args.anchor_mode == "default":
        params = copy.deepcopy(runner.BASELINE_PARAMS)
    else:
        params = copy.deepcopy(source.get("incumbent_params"))
    if not isinstance(params, Mapping) or len(params) != 7:
        raise RuntimeError("anchor parameters are incomplete")
    config = load(args.config)
    capability = {
        name: float(config["capability"][name]) for name in RESOURCES
    }

    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    state_path = args.output / "diagnostic_state.json"
    lock_path = args.output.parent / ".tpch_v38_anchor.lock"
    state: Dict[str, Any] = {
        "schema_version": "tpch_current_epoch_anchor_diagnostic_state_v1",
        "status": "starting",
        "diagnostic_only": True,
        "maximum_real_replay_invocations": PHYSICAL_CALL_CAP,
        "physical_call_count": 0,
        "valid_replay_count": 0,
        "technical_failure_count": 0,
        "accepted_count": 0,
        "formal_state_mutation_allowed": False,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "baseline_restored": False,
        "observations": [],
    }
    atomic_json(state_path, state)
    observations: list[Dict[str, Any]] = []
    failure: Exception | None = None
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        for ordinal in range(1, PHYSICAL_CALL_CAP + 1):
            if failure is not None:
                break
            baseline_restored = False
            state["physical_call_count"] = ordinal
            state["status"] = "running"
            atomic_json(state_path, state)
            try:
                verify(
                    args.source_formal_state,
                    args.source_formal_state_sha256,
                    "formal state before replay",
                )
                read_accepted(args.accepted_counter)
                apply_verification = runner.apply_params(
                    args.container, args.database, params
                )
                run_root = args.output / f"physical_call_{ordinal:02d}"
                returncode, output = runner.run_workload(
                    args.workload, run_root, args.timeout_seconds
                )
                payload = runner.extract_payload(output)
                resource, utilization, score = runner.classify_payload(
                    payload, returncode, capability
                )
                observation = {
                    "ordinal": ordinal,
                    "technical_failure": False,
                    "valid_replay": True,
                    "candidate_params": copy.deepcopy(dict(params)),
                    "actual_resource": finite_resource(resource),
                    "actual_utilization": {
                        name: float(utilization[name]) for name in RESOURCES
                    },
                    "actual_score": float(score),
                    "parameter_verification": apply_verification,
                }
                if not math.isfinite(observation["actual_score"]):
                    raise RuntimeError("anchor score is not finite")
                observations.append(observation)
                state["valid_replay_count"] = len(observations)
                state["observations"] = copy.deepcopy(observations)
            except Exception as exc:
                failure = exc
                state["technical_failure_count"] += 1
                state["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    baseline_verification = runner.apply_params(
                        args.container, args.database, runner.BASELINE_PARAMS
                    )
                    baseline_restored = True
                    state["last_baseline_verification"] = baseline_verification
                    if observations and observations[-1]["ordinal"] == ordinal:
                        observations[-1]["baseline_restored_after_observation"] = True
                        observations[-1]["baseline_verification"] = (
                            baseline_verification
                        )
                        state["observations"] = copy.deepcopy(observations)
                except Exception as exc:
                    failure = failure or exc
                    state["technical_failure_count"] = max(
                        1, int(state["technical_failure_count"])
                    )
                    state["baseline_restore_error"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
                state["baseline_restored"] = baseline_restored
                atomic_json(state_path, state)
            if not baseline_restored:
                break

        source_unchanged = sha256(args.source_formal_state) == source_sha_before
        try:
            accepted_unchanged = read_accepted(args.accepted_counter) == 0
        except (OSError, ValueError, RuntimeError):
            accepted_unchanged = False
        if not (
            failure is None
            and len(observations) == PHYSICAL_CALL_CAP
            and int(state["physical_call_count"]) == PHYSICAL_CALL_CAP
            and int(state["valid_replay_count"]) == PHYSICAL_CALL_CAP
            and int(state["technical_failure_count"]) == 0
            and state["baseline_restored"] is True
            and source_unchanged
            and accepted_unchanged
        ):
            state["status"] = "failed_closed"
            state["source_formal_state_mutated"] = not source_unchanged
            state["accepted_counter_mutated"] = not accepted_unchanged
            atomic_json(state_path, state)
            return 2

        try:
            anchor_state = build_anchor_state(
                source,
                params,
                observations,
                {
                    "anchor_mode": args.anchor_mode,
                    "source_formal_state": str(args.source_formal_state),
                    "source_formal_state_sha256": source_sha_before,
                    "runner_module": str(args.runner_module),
                    "runner_module_sha256": args.runner_module_sha256,
                    "workload": str(args.workload),
                    "workload_sha256": args.workload_sha256,
                    "workload_driver": str(args.workload_driver),
                    "workload_driver_sha256": args.workload_driver_sha256,
                    "runtime_dependencies": runtime_dependencies,
                    "config": str(args.config),
                    "config_sha256": args.config_sha256,
                },
                args.maximum_score_span,
                args.maximum_resource_factor,
            )
        except Exception as exc:
            state["status"] = "failed_closed_unstable_anchor"
            state["error"] = f"{type(exc).__name__}: {exc}"
            atomic_json(state_path, state)
            return 3
        anchor_path = args.output / "current_anchor_state.json"
        atomic_json(anchor_path, anchor_state)
        state.update(
            {
                "status": "complete",
                "baseline_restored": True,
                "current_anchor_state": str(anchor_path),
                "current_anchor_state_sha256": sha256(anchor_path),
                "source_formal_state_mutated": False,
                "accepted_counter_mutated": False,
            }
        )
        atomic_json(state_path, state)
    print(
        json.dumps(
            {
                "status": "ready",
                "physical_calls": PHYSICAL_CALL_CAP,
                "valid_replays": PHYSICAL_CALL_CAP,
                "accepted_count": 0,
                "anchor_state": str(anchor_path),
                "anchor_state_sha256": sha256(anchor_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
