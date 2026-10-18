#!/usr/bin/env python3
"""Autonomously continue TPC-H from formal003 to fresh accepted target 10.

The controller runs locally on node01 and never opens SSH.  Every acquisition
cycle uses a clean formal resume state for incumbent/range inheritance, then
runs a novel probe, an exact-point repeat, a two-observation robust repeat,
the fixed quality gate, and only then a formal Top-5 replay.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, Iterable, Mapping, Sequence


B = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
ROOT = B / "tpch_final_algorithm_fresh10_v58_20260731T2340"
LOOP_ROOT = ROOT / "target10_loop_v26"
COMMON_PATH = Path("/tmp/tpch_fresh10_robust_validation_v22.py")
SUCCESSOR_BUILDER = Path(
    "/tmp/build_tpch_autonomous_successor_top5_v4_resume_range.py"
)
EXACT_REPEAT_BUILDER = Path(
    "/tmp/tpch_fresh0_handover_v19/build_tpch_exact_plan_repeat_validation.py"
)
ROBUST_BUILDER = Path("/tmp/build_tpch_two_observation_robust_repeat_v2.py")
ANALYZER = Path("/tmp/tpch_fresh0_handover_v19/analyze_top5_validation_gate.py")
EXPANSION = (
    B
    / "gradient_debug_round57d_multi_neighbor_expansion_v17_20260731T2300"
    / "expanded_candidates.json"
)
PYTHON = Path("/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11")
ACCEPTED_COUNTER = ROOT / "formal_search_accepted_counter.txt"
INITIAL_PREVIOUS_SELECTION = ROOT / "robust_repeat006_top5.a.json"
INITIAL_PREVIOUS_RUN = ROOT / "diagnostic_validation006"
INITIAL_FORMAL_RUN = ROOT / "formal_batch003"
INITIAL_FALLBACK_FORMAL_RUN = ROOT / "formal_batch002"
INITIAL_HANDOVER = ROOT / "successor003_recovery_v27_handover.json"
TARGET = 10
CAP = 5
MAX_CYCLES = 40

EXPECTED = {
    "common": "e56f3db493201e7aec9b944df67e8b40c5226114a87c85adad7b5def0532e578",
    "successor_builder": "1661cb8363e755a180af4367fb69d26d842aec3f27aa7f1491c3a34a667c7d0d",
    "exact_repeat_builder": "4f1684e73c9c91f32d94655053a4c6af5a8f8f0d2d2eb8a94363eaa24b48cfe7",
    "robust_builder": "4a5014c9cd5aaad138c108c1e0ad0087f6dfcf7ef91587d2e1829fefe46f926e",
    "analyzer": "49936cbc4319ef6055d6653c276892cd9df408a98d8560460c3e4f0bd7e37d30",
    "expansion": "c861212c82fcc3abbe8f976c3257c91e87b6dba9fa078108a98bff1f63aa9f14",
}


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
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def verify(path: Path, expected: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise RuntimeError(f"SHA mismatch: {path}")


def import_common() -> Any:
    specification = importlib.util.spec_from_file_location(
        "tpch_target10_common", COMMON_PATH
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load TPC-H common controller")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def run(
    common: Any,
    argv: Sequence[str],
    log: Path,
    environment: Mapping[str, str],
) -> None:
    common.run(argv, log, environment)


def accepted_counter() -> int:
    return int(ACCEPTED_COUNTER.read_text(encoding="utf-8").strip())


def validate_formal_completion(
    run_root: Path,
    accepted_before: int,
    *,
    allow_wait: bool = True,
) -> tuple[int, Path, bool]:
    state_path = run_root / "formal_state.json"
    deadline = time.monotonic() + (6 * 60 * 60 if allow_wait else 0)
    while True:
        if state_path.is_file():
            state = load(state_path)
            if state.get("status") == "complete":
                summary_path = run_root / "summary.json"
                if not summary_path.is_file():
                    raise RuntimeError("completed formal batch lacks summary")
                summary = load(summary_path)
                accepted_after = int(state.get("accepted_count", -1))
                physical = int(state.get("physical_call_count", -1))
                valid = int(state.get("valid_replay_count", -1))
                technical = int(state.get("technical_failure_count", -1))
                if not (
                    0 <= accepted_before <= accepted_after <= TARGET
                    and accepted_counter() == accepted_after
                    and state.get("baseline_restored") is True
                    and summary.get("baseline_restored") is True
                    and int(summary.get("accepted_count", -1)) == accepted_after
                    and int(summary.get("physical_call_count", -1)) == physical
                    and int(summary.get("valid_replay_count", -1)) == valid
                    and int(summary.get("technical_failure_count", -1))
                    == technical
                ):
                    raise RuntimeError("formal batch completion audit mismatch")
                clean = technical == 0 and valid == physical and 1 <= physical <= CAP
                if clean and accepted_after < TARGET and physical != CAP:
                    raise RuntimeError("non-terminal clean formal batch is not full Top-5")
                if not clean and state.get("baseline_restored") is not True:
                    raise RuntimeError("technical formal failure did not restore baseline")
                return accepted_after, state_path, clean
            if state.get("status") not in {"starting", "running"}:
                raise RuntimeError(f"formal batch status={state.get('status')}")
        if not allow_wait or time.monotonic() >= deadline:
            raise RuntimeError(f"timed out waiting for formal batch: {run_root}")
        time.sleep(15)


def resolve_initial_authority(
    common: Any,
    accepted_before: int,
) -> tuple[int, Path, bool]:
    """Wait for formal003 or route a failed gate to formal002 authority."""
    deadline = time.monotonic() + 6 * 60 * 60
    while time.monotonic() < deadline:
        formal_state = INITIAL_FORMAL_RUN / "formal_state.json"
        if formal_state.is_file():
            return validate_formal_completion(
                INITIAL_FORMAL_RUN,
                accepted_before,
            )
        if INITIAL_HANDOVER.is_file():
            handover = load(INITIAL_HANDOVER)
            status = handover.get("status")
            if status == "entry_gate_failed":
                if (
                    handover.get("formal_started") is not False
                    or handover.get("entry_gate_passed") is not False
                ):
                    raise RuntimeError("invalid failed-gate handover")
                common.validate_clean_diagnostic(
                    load(INITIAL_PREVIOUS_RUN / "diagnostic_state.json"),
                    accepted_before,
                )
                return validate_formal_completion(
                    INITIAL_FALLBACK_FORMAL_RUN,
                    accepted_before,
                    allow_wait=False,
                )
            if status != "formal_batch003_started":
                raise RuntimeError(f"invalid successor003 handover: {status}")
        time.sleep(15)
    raise RuntimeError("timed out waiting for successor003 handover")


def patch_accepted_start(path: Path, accepted: int) -> None:
    payload = load(path)
    payload["accepted_count_start"] = accepted
    payload["inherited_accepted_count"] = accepted
    payload["historical_accepted_excluded"] = 1
    atomic_json(path, payload)


def dynamic_config(common: Any, cycle_root: Path, accepted: int) -> Path:
    source = common.CONFIG
    verify(source, common.EXPECTED["config"])
    payload = load(source)
    protocol = payload.get("formal_outer_protocol")
    if not isinstance(protocol, dict):
        raise RuntimeError("source config lacks formal_outer_protocol")
    protocol["formal_search_accepted_count_start"] = accepted
    first = cycle_root / "config.a.json"
    second = cycle_root / "config.b.json"
    atomic_json(first, payload)
    atomic_json(second, payload)
    if first.read_bytes() != second.read_bytes():
        raise RuntimeError("dynamic config generation is not deterministic")
    return first


def selection_paths(root: Path) -> Iterable[Path]:
    patterns = (
        "*top5*.json",
        "formal_batch*_package/top5_formal.json",
        "target10_loop_v26/cycle_*/*.json",
    )
    seen: set[str] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            key = str(path.resolve())
            if key in seen or not path.is_file():
                continue
            try:
                payload = load(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload.get("top5"), list) and len(payload["top5"]) == CAP:
                seen.add(key)
                yield path.resolve()


def build_probe(
    common: Any,
    cycle_root: Path,
    previous_selection: Path,
    previous_run: Path,
    resume_state: Path,
    accepted: int,
    environment: Mapping[str, str],
) -> Path:
    output_a = cycle_root / "probe_top5.a.json"
    output_b = cycle_root / "probe_top5.b.json"
    base = [
        str(PYTHON),
        "-u",
        str(SUCCESSOR_BUILDER),
        "--previous-selection",
        str(previous_selection),
        "--previous-summary",
        str(previous_run / "diagnostic_summary.json"),
        "--expanded-candidates",
        str(EXPANSION),
        "--minimum-score-span",
        "0.002",
        "--resume-state",
        str(resume_state),
    ]
    previous_resolved = previous_selection.resolve()
    for path in selection_paths(ROOT):
        if path != previous_resolved and path not in {output_a, output_b}:
            base.extend(["--additional-exclusion-selection", str(path)])
    run(common, [*base, "--output", str(output_a)], cycle_root / "probe_builder_a.log", environment)
    run(common, [*base, "--output", str(output_b)], cycle_root / "probe_builder_b.log", environment)
    patch_accepted_start(output_a, accepted)
    patch_accepted_start(output_b, accepted)
    if output_a.read_bytes() != output_b.read_bytes():
        raise RuntimeError("probe builder is not deterministic")
    roles = [str(item.get("selection_role")) for item in load(output_a)["top5"]]
    if roles not in (["probe"] * CAP, ["trusted"] * 3 + ["probe"] * 2):
        raise RuntimeError(f"invalid probe roles: {roles}")
    return output_a


def diagnostic_argv(
    common: Any,
    selection: Path,
    output: Path,
    config: Path,
    resume_state: Path,
) -> list[str]:
    return [
        str(PYTHON),
        "-u",
        str(common.DIAGNOSTIC_RUNNER),
        "--diagnostic-only",
        "--selection",
        str(selection),
        "--selection-sha256",
        sha256(selection),
        "--gate-summary",
        str(common.SOURCE_GATE),
        "--gate-summary-sha256",
        common.EXPECTED["source_gate"],
        "--baseline-summary",
        str(common.BASELINE),
        "--baseline-summary-sha256",
        common.EXPECTED["baseline"],
        "--config",
        str(config),
        "--config-sha256",
        sha256(config),
        "--workload",
        str(common.WORKLOAD),
        "--workload-sha256",
        common.EXPECTED["workload"],
        "--accepted-counter",
        str(ACCEPTED_COUNTER),
        "--resume-state",
        str(resume_state),
        "--resume-state-sha256",
        sha256(resume_state),
        "--selection-start-rank",
        "1",
        "--maximum-real-replay-invocations",
        "5",
        "--output",
        str(output),
        "--container",
        common.CONTAINER,
        "--database",
        common.DATABASE,
        "--timeout-seconds",
        "900",
    ]


def run_clean_diagnostic(
    common: Any,
    selection: Path,
    output: Path,
    config: Path,
    resume_state: Path,
    accepted: int,
    log: Path,
    environment: Mapping[str, str],
) -> None:
    run(
        common,
        diagnostic_argv(common, selection, output, config, resume_state),
        log,
        environment,
    )
    common.validate_clean_diagnostic(load(output / "diagnostic_state.json"), accepted)
    if accepted_counter() != accepted:
        raise RuntimeError("diagnostic changed accepted counter")


def build_exact_repeat(
    common: Any,
    cycle_root: Path,
    probe: Path,
    probe_run: Path,
    accepted: int,
    environment: Mapping[str, str],
) -> Path:
    first = cycle_root / "exact_repeat_top5.a.json"
    second = cycle_root / "exact_repeat_top5.b.json"
    base = [
        str(PYTHON),
        "-u",
        str(EXACT_REPEAT_BUILDER),
        "--selection",
        str(probe),
        "--summary",
        str(probe_run / "diagnostic_summary.json"),
        "--minimum-score-span",
        "0.002",
        "--output",
    ]
    run(common, [*base, str(first)], cycle_root / "exact_builder_a.log", environment)
    run(common, [*base, str(second)], cycle_root / "exact_builder_b.log", environment)
    patch_accepted_start(first, accepted)
    patch_accepted_start(second, accepted)
    if first.read_bytes() != second.read_bytes():
        raise RuntimeError("exact-repeat builder is not deterministic")
    common.validate_selection(load(first), ["trusted"] * 3 + ["probe"] * 2)
    return first


def build_robust_repeat(
    common: Any,
    cycle_root: Path,
    probe: Path,
    probe_run: Path,
    repeat: Path,
    repeat_run: Path,
    accepted: int,
    environment: Mapping[str, str],
) -> Path:
    first = cycle_root / "robust_top5.a.json"
    second = cycle_root / "robust_top5.b.json"
    base = [
        str(PYTHON),
        "-u",
        str(ROBUST_BUILDER),
        "--source-selection",
        str(probe),
        "--source-summary",
        str(probe_run / "diagnostic_summary.json"),
        "--repeat-selection",
        str(repeat),
        "--repeat-summary",
        str(repeat_run / "diagnostic_summary.json"),
        "--maximum-trusted-score-span",
        "0.01",
        "--maximum-trusted-resource-factor",
        "1.5",
        "--output",
    ]
    run(common, [*base, str(first)], cycle_root / "robust_builder_a.log", environment)
    run(common, [*base, str(second)], cycle_root / "robust_builder_b.log", environment)
    patch_accepted_start(first, accepted)
    patch_accepted_start(second, accepted)
    if first.read_bytes() != second.read_bytes():
        raise RuntimeError("robust-repeat builder is not deterministic")
    common.validate_selection(load(first), ["trusted"] * 3 + ["probe"] * 2)
    return first


def analyze_gate(
    common: Any,
    cycle_root: Path,
    selection: Path,
    validation_run: Path,
    environment: Mapping[str, str],
) -> Path:
    output = cycle_root / "entry_gate.json"
    run(
        common,
        [
            str(PYTHON),
            "-u",
            str(ANALYZER),
            "--selection",
            str(selection),
            "--observations",
            str(validation_run / "diagnostic_summary.json"),
            "--output",
            str(output),
        ],
        cycle_root / "entry_gate.log",
        environment,
    )
    return output


def promote_formal(
    common: Any,
    cycle_root: Path,
    robust: Path,
    robust_run: Path,
    analysis_path: Path,
    accepted: int,
    config: Path,
) -> tuple[Path, Path, Path]:
    formal_package = cycle_root / "formal_package"
    formal_run = cycle_root / "formal_run"
    common.ROBUST_SELECTION_A = robust
    common.VALIDATION_RUN = robust_run
    common.FORMAL_PACKAGE = formal_package
    common.FORMAL_RUN = formal_run
    selection, gate = common.promote_formal(load(robust), load(analysis_path))
    gate_payload = load(gate)
    gate_payload["accepted_count"] = accepted
    gate_payload["inherited_accepted_count"] = accepted
    gate_payload["historical_accepted_excluded"] = 1
    bindings = gate_payload.setdefault("bindings", {})
    bindings["fresh_config_sha256"] = sha256(config)
    bindings["dynamic_config_sha256"] = sha256(config)
    gate_payload["resume_state_required"] = True
    atomic_json(gate, gate_payload)
    return selection, gate, formal_run


def run_formal(
    common: Any,
    selection: Path,
    gate: Path,
    formal_run: Path,
    config: Path,
    resume_state: Path,
    environment: Mapping[str, str],
) -> None:
    formal_run.mkdir(mode=0o700)
    run(
        common,
        [
            str(PYTHON),
            "-u",
            str(common.FORMAL_RUNNER),
            "--selection",
            str(selection),
            "--selection-sha256",
            sha256(selection),
            "--gate-summary",
            str(gate),
            "--gate-summary-sha256",
            sha256(gate),
            "--baseline-summary",
            str(common.BASELINE),
            "--baseline-summary-sha256",
            common.EXPECTED["baseline"],
            "--config",
            str(config),
            "--config-sha256",
            sha256(config),
            "--workload",
            str(common.WORKLOAD),
            "--workload-sha256",
            common.EXPECTED["workload"],
            "--accepted-counter",
            str(ACCEPTED_COUNTER),
            "--resume-state",
            str(resume_state),
            "--resume-state-sha256",
            sha256(resume_state),
            "--output",
            str(formal_run),
            "--container",
            common.CONTAINER,
            "--database",
            common.DATABASE,
            "--timeout-seconds",
            "900",
        ],
        formal_run / "driver.log",
        environment,
    )


def main() -> int:
    LOOP_ROOT.mkdir(mode=0o700, exist_ok=True)
    state_path = LOOP_ROOT / "controller_state.json"
    lock_path = LOOP_ROOT / "controller.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name, path in (
            ("common", COMMON_PATH),
            ("successor_builder", SUCCESSOR_BUILDER),
            ("exact_repeat_builder", EXACT_REPEAT_BUILDER),
            ("robust_builder", ROBUST_BUILDER),
            ("analyzer", ANALYZER),
            ("expansion", EXPANSION),
        ):
            verify(path, EXPECTED[name])
        if state_path.exists():
            raise RuntimeError("target10 loop state already exists; refuse implicit resume")
        common = import_common()
        environment = common.runtime_environment()
        initial_accepted_before = accepted_counter()
        state: Dict[str, Any] = {
            "schema_version": "tpch_fresh10_target10_loop_v26",
            "status": "waiting_formal003",
            "accepted_count": initial_accepted_before,
            "accepted_target": TARGET,
            "historical_accepted_excluded": 1,
            "next_cycle": 4,
            "cycles": [],
        }
        atomic_json(state_path, state)

        accepted, resume_state, clean = resolve_initial_authority(
            common,
            initial_accepted_before,
        )
        if not clean:
            raise RuntimeError("formal003 is not a clean continuation authority")
        previous_selection = INITIAL_PREVIOUS_SELECTION
        previous_run = INITIAL_PREVIOUS_RUN
        state["accepted_count"] = accepted
        state["resume_state"] = str(resume_state)
        atomic_json(state_path, state)

        for cycle in range(4, 4 + MAX_CYCLES):
            if accepted >= TARGET:
                state["status"] = "target_reached"
                atomic_json(state_path, state)
                return 0
            cycle_root = LOOP_ROOT / f"cycle_{cycle:03d}"
            cycle_root.mkdir(mode=0o700)
            state["status"] = "preparing_cycle"
            state["next_cycle"] = cycle
            atomic_json(state_path, state)
            config = dynamic_config(common, cycle_root, accepted)
            probe = build_probe(
                common,
                cycle_root,
                previous_selection,
                previous_run,
                resume_state,
                accepted,
                environment,
            )
            probe_run = cycle_root / "diagnostic_probe"
            run_clean_diagnostic(
                common,
                probe,
                probe_run,
                config,
                resume_state,
                accepted,
                cycle_root / "diagnostic_probe.log",
                environment,
            )
            repeat = build_exact_repeat(
                common, cycle_root, probe, probe_run, accepted, environment
            )
            repeat_run = cycle_root / "diagnostic_exact_repeat"
            run_clean_diagnostic(
                common,
                repeat,
                repeat_run,
                config,
                resume_state,
                accepted,
                cycle_root / "diagnostic_exact_repeat.log",
                environment,
            )
            robust = build_robust_repeat(
                common,
                cycle_root,
                probe,
                probe_run,
                repeat,
                repeat_run,
                accepted,
                environment,
            )
            robust_run = cycle_root / "diagnostic_robust_repeat"
            run_clean_diagnostic(
                common,
                robust,
                robust_run,
                config,
                resume_state,
                accepted,
                cycle_root / "diagnostic_robust_repeat.log",
                environment,
            )
            analysis_path = analyze_gate(
                common, cycle_root, robust, robust_run, environment
            )
            analysis = load(analysis_path)
            cycle_audit: Dict[str, Any] = {
                "cycle": cycle,
                "accepted_before": accepted,
                "probe_selection_sha256": sha256(probe),
                "repeat_selection_sha256": sha256(repeat),
                "robust_selection_sha256": sha256(robust),
                "entry_gate_sha256": sha256(analysis_path),
                "entry_gate_passed": analysis.get("passed") is True,
            }
            previous_selection = robust
            previous_run = robust_run
            if analysis.get("passed") is not True:
                cycle_audit["formal_started"] = False
                cycle_audit["accepted_after"] = accepted
                state["cycles"].append(cycle_audit)
                state["next_cycle"] = cycle + 1
                atomic_json(state_path, state)
                continue

            formal_selection, gate, formal_run = promote_formal(
                common,
                cycle_root,
                robust,
                robust_run,
                analysis_path,
                accepted,
                config,
            )
            formal_error = None
            try:
                run_formal(
                    common,
                    formal_selection,
                    gate,
                    formal_run,
                    config,
                    resume_state,
                    environment,
                )
            except RuntimeError as exc:
                formal_error = str(exc)
            accepted_after, formal_state, formal_clean = validate_formal_completion(
                formal_run,
                accepted,
                allow_wait=False,
            )
            cycle_audit.update(
                {
                    "formal_started": True,
                    "formal_selection_sha256": sha256(formal_selection),
                    "formal_gate_sha256": sha256(gate),
                    "formal_clean": formal_clean,
                    "formal_error": formal_error,
                    "accepted_after": accepted_after,
                    "formal_state_sha256": sha256(formal_state),
                }
            )
            state["cycles"].append(cycle_audit)
            if formal_clean:
                accepted = accepted_after
                resume_state = formal_state
                state["accepted_count"] = accepted
                state["resume_state"] = str(resume_state)
            elif accepted_after != accepted:
                raise RuntimeError("technical formal batch changed accepted count")
            state["next_cycle"] = cycle + 1
            atomic_json(state_path, state)

        state["status"] = "blocked_max_cycles"
        atomic_json(state_path, state)
        raise RuntimeError("TPC-H target10 loop exhausted maximum cycles")


if __name__ == "__main__":
    raise SystemExit(main())
