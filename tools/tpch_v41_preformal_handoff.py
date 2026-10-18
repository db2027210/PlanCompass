#!/usr/bin/env python3
"""Adopt TPC-H v40 diagnostics into a robust-four-observation v41 campaign.

No v40 diagnostic is replayed.  The final clean v40 diagnostic is resealed and
gated with zero workload calls.  A passing gate enters a distinct v41 formal
root through the robust-four-observation runner; a failed gate becomes training
evidence for the autonomous next acquisition cycle.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, Mapping


B = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
V40_ROOT = B / "tpch_final_algorithm_fresh0_v40_retest_compat_20260803"
V40_LOOP = V40_ROOT / "target10_loop_v40"
V40_COUNTER = V40_ROOT / "formal_search_accepted_counter.txt"
V41_ROOT = B / "tpch_final_algorithm_fresh0_v41_robust4_handoff_20260803"
V41_LOOP = V41_ROOT / "target10_loop_v41"
V41_COUNTER = V41_ROOT / "formal_search_accepted_counter.txt"
ADOPTION_ROOT = V41_ROOT / "adopted_v40_diagnostic"
ADOPTED_FORMAL_ROOT = V41_ROOT / "adopted_v40_formal"

V26 = Path("/tmp/tpch_fresh0_target10_loop_v36_base.py")
V27 = Path("/tmp/tpch_fresh10_target10_loop_v27.py")
V30 = Path("/tmp/tpch_fresh10_target10_loop_v30.py")
V32 = Path("/tmp/tpch_fresh10_target10_loop_v32_v6_hierarchical.py")
V33 = Path("/tmp/tpch_fresh10_target10_loop_v33_prethird_bound.py")
BUILDER = Path("/tmp/build_tpch_autonomous_successor_top5_v9_bounded_retest.py")
EXPANDER = Path("/tmp/tpch_exact_regime_neighbor_expand_v5_bounded.py")
DIAGNOSTIC_RUNNER = Path(
    "/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v40.py"
)
FORMAL_RUNNER = Path(
    "/tmp/run_tpch_formal_exact_plan_regime_batch_v41_robust4.py"
)

EXPECTED = {
    V26: "34107e91b4bcfd0b2fe8d2911a0855e1512fc881665f4db3c3cb67618bd055fb",
    V27: "d8e1672b4aceea5065f612170d57c1b16917a1dce9bf039fb3af274be4492589",
    V30: "384f2ce8f944525711d706c1e85493bfe61ed075d20d6794f61caed3dd67d7f8",
    V32: "54ac22227f190bfbbc1f2889927cba9500cbe1bd6f4347287d69c3647d6e98b5",
    V33: "e5c18e3ebe7b8152e12ada134b86f50b32edc610f86346030c37aba5ebd688a0",
    BUILDER: "3f2514db76a9e86484029b2bd72162c96f190f5f71af218543335e1b4c93208a",
    EXPANDER: "dce066d239ca68eb95ec10bd39a77e798212395df782ae850ffe7d27d5346a78",
    DIAGNOSTIC_RUNNER: (
        "c3d352b980daf403a3344b7381ad5699215f6b2ba59b2a40c10b6e71f91a908d"
    ),
    FORMAL_RUNNER: (
        "2e8daea587b6d158269dbbe8767b086c0882aa0cd92650a3860c39f38e649eeb"
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def verify_static_dependencies() -> None:
    for path, expected in EXPECTED.items():
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"TPC-H v41 dependency SHA mismatch: {path}")


def clean_diagnostic(
    selection: Path,
    run_root: Path,
    accepted: int = 0,
) -> Dict[str, Any]:
    state_path = run_root / "diagnostic_state.json"
    summary_path = run_root / "diagnostic_summary.json"
    if not state_path.is_file() or not summary_path.is_file():
        raise RuntimeError(f"diagnostic artifacts are incomplete: {run_root}")
    state = load(state_path)
    summary = load(summary_path)
    selection_sha = sha256(selection)
    immutable_fields = (
        "accepted_update_applied",
        "incumbent_update_applied",
        "range_update_applied",
        "gradient_update_applied",
    )
    if not (
        state.get("status") == "complete"
        and state.get("diagnostic_only") is True
        and int(state.get("accepted_count", -1)) == accepted
        and int(state.get("physical_call_count", -1)) == 5
        and int(state.get("valid_replay_count", -1)) == 5
        and int(state.get("technical_failure_count", -1)) == 0
        and state.get("baseline_restored") is True
        and state.get("accepted_update_allowed") is False
        and state.get("incumbent_update_allowed") is False
        and state.get("range_update_allowed") is False
        and state.get("gradient_update_allowed") is False
        and state.get("selection_sha256") == selection_sha
        and int(summary.get("accepted_count", -1)) == accepted
        and int(summary.get("physical_call_count", -1)) == 5
        and int(summary.get("valid_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restored") is True
        and summary.get("selection_sha256") == selection_sha
        and all(summary.get(field) is False for field in immutable_fields)
    ):
        raise RuntimeError(f"v40 diagnostic is not clean state-neutral 5/5/0: {run_root}")
    return {"state": state, "summary": summary}


def wait_for_final_v40_diagnostic(timeout_seconds: int) -> tuple[Path, Path]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for cycle in sorted(V40_LOOP.glob("cycle_*"), reverse=True):
            selection = cycle / "robust_top5.a.json"
            run_root = cycle / "diagnostic_robust_repeat"
            state_path = run_root / "diagnostic_state.json"
            if not selection.is_file() or not state_path.is_file():
                continue
            state = load(state_path)
            if state.get("status") == "complete":
                clean_diagnostic(selection, run_root)
                return selection, run_root
            if state.get("status") not in {"starting", "running"}:
                raise RuntimeError(f"v40 final diagnostic status={state.get('status')}")
        time.sleep(15)
    raise RuntimeError("timed out waiting for v40 final clean diagnostic")


def executed_v40_selections(cycle: Path) -> list[Path]:
    bindings = (
        (cycle / "probe_top5.a.json", cycle / "diagnostic_probe"),
        (cycle / "exact_repeat_top5.a.json", cycle / "diagnostic_exact_repeat"),
        (cycle / "robust_top5.a.json", cycle / "diagnostic_robust_repeat"),
    )
    paths = []
    for selection, run_root in bindings:
        clean_diagnostic(selection, run_root)
        paths.append(selection.resolve())
    return paths


def run_zero_call(
    common: Any,
    argv: list[str],
    log: Path,
    environment: Mapping[str, str],
) -> None:
    forbidden = {
        str(common.WORKLOAD),
        str(common.DIAGNOSTIC_RUNNER),
        str(common.FORMAL_RUNNER),
    }
    if any(value in forbidden for value in argv):
        raise RuntimeError("v41 zero-call adoption attempted a replay runner")
    common.run(argv, log, environment)


def reseal_and_gate(
    v26: Any,
    v27: Any,
    v30: Any,
    v32: Any,
    common: Any,
    selection: Path,
    run_root: Path,
    anchor_state: Path,
    environment: Mapping[str, str],
) -> tuple[Path, Path, Path]:
    ADOPTION_ROOT.mkdir(mode=0o700)
    outputs = []
    for label in ("a", "b"):
        root = ADOPTION_ROOT / label
        root.mkdir(mode=0o700)
        output_selection = root / "selection.json"
        output_summary = root / "diagnostic_summary.json"
        output_state = root / "diagnostic_state.json"
        run_zero_call(
            common,
            [
                str(v26.PYTHON),
                "-u",
                str(v32.RESEALER),
                "--selection",
                str(selection),
                "--third-observation-summary",
                str(run_root / "diagnostic_summary.json"),
                "--maximum-trusted-score-span",
                "0.01",
                "--adaptive-score-span-cap",
                "0.05",
                "--maximum-trusted-resource-factor",
                "1.5",
                "--output",
                str(output_selection),
                "--output-summary",
                str(output_summary),
                "--output-state",
                str(output_state),
            ],
            ADOPTION_ROOT / f"reseal_{label}.log",
            environment,
        )
        outputs.append((output_selection, output_summary, output_state))
    if any(
        outputs[0][index].read_bytes() != outputs[1][index].read_bytes()
        for index in range(3)
    ):
        raise RuntimeError("v41 adopted reseal is not deterministic")
    resealed, summary, state = outputs[0]
    common.validate_selection(load(resealed), ["trusted"] * 3 + ["probe"] * 2)
    v30.validate_resealed_summary(v26, resealed, resealed.parent)

    gate_path = ADOPTION_ROOT / "entry_gate.json"
    run_zero_call(
        common,
        [
            str(v26.PYTHON),
            "-u",
            str(v26.ANALYZER),
            "--selection",
            str(resealed),
            "--observations",
            str(summary),
            "--output",
            str(gate_path),
        ],
        ADOPTION_ROOT / "entry_gate.log",
        environment,
    )
    gate = load(gate_path)
    feasibility = v32.acceptance_feasibility(v26, resealed, anchor_state)
    feasibility_path = ADOPTION_ROOT / "acceptance_feasibility.json"
    v27.atomic_json(feasibility_path, feasibility)
    rank_gate_passed = gate.get("passed") is True
    gate.setdefault("checks", {})["trusted_acceptance_feasible"] = feasibility[
        "passed"
    ]
    gate["rank_gate_passed_before_acceptance_feasibility"] = rank_gate_passed
    gate["passed"] = rank_gate_passed and feasibility["passed"] is True
    gate["acceptance_feasibility"] = {
        "path": str(feasibility_path),
        "sha256": sha256(feasibility_path),
        **feasibility,
    }
    gate["v41_zero_call_adoption"] = {
        "source_selection": str(selection),
        "source_selection_sha256": sha256(selection),
        "source_summary_sha256": sha256(run_root / "diagnostic_summary.json"),
        "resealed_selection_sha256": sha256(resealed),
        "new_diagnostic_calls": 0,
        "new_physical_calls": 0,
        "formal_state_mutated": False,
    }
    v27.atomic_json(gate_path, gate)
    return resealed, resealed.parent, gate_path


def selection_paths(
    original: Any,
    root: Path,
    loop_root: Path,
    adopted: Iterable[Path],
) -> Iterable[Path]:
    seen = set()
    for path in [*original(root), *adopted, *loop_root.glob("cycle_*/*.json")]:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        try:
            payload = load(resolved)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload.get("top5"), list) and len(payload["top5"]) == 5:
            seen.add(resolved)
            yield resolved


def configure_controller(
    anchor_state: Path,
    initial_selection: Path,
    initial_run: Path,
    executed_history: list[Path],
    initial_formal_run: Path | None,
) -> tuple[Any, Any]:
    v26 = import_module("tpch_v41_v26", V26)
    v27 = import_module("tpch_v41_v27", V27)
    v30 = import_module("tpch_v41_v30", V30)
    v32 = import_module("tpch_v41_v32", V32)
    v33 = import_module("tpch_v41_v33", V33)
    for path, expected in (
        (v26.COMMON_PATH, v26.EXPECTED["common"]),
        (v26.EXACT_REPEAT_BUILDER, v26.EXPECTED["exact_repeat_builder"]),
        (v26.ANALYZER, v26.EXPECTED["analyzer"]),
        (v32.ROBUST_BUILDER, v32.ROBUST_BUILDER_SHA256),
        (v32.RESEALER, v32.RESEALER_SHA256),
        (v32.SELECTOR, v32.SELECTOR_SHA256),
    ):
        v26.verify(path, expected)
    v26.ROOT = V41_ROOT
    v26.LOOP_ROOT = V41_LOOP
    v26.ACCEPTED_COUNTER = V41_COUNTER
    v26.INITIAL_PREVIOUS_SELECTION = initial_selection
    v26.INITIAL_PREVIOUS_RUN = initial_run
    v26.MAX_CYCLES = 100
    v26.SUCCESSOR_BUILDER = BUILDER
    v26.EXPECTED["successor_builder"] = EXPECTED[BUILDER]
    v26.ROBUST_BUILDER = v32.ROBUST_BUILDER
    v26.EXPECTED["robust_builder"] = v32.ROBUST_BUILDER_SHA256
    v32.SUCCESSOR_BUILDER = BUILDER
    v32.SUCCESSOR_BUILDER_SHA256 = EXPECTED[BUILDER]
    v32.EXPANDER = EXPANDER
    v32.EXPANDER_SHA256 = EXPECTED[EXPANDER]
    original_selection_paths = v26.selection_paths
    original_import_common = v26.import_common
    cache: Dict[str, Any] = {}

    def import_common() -> Any:
        if "common" in cache:
            return cache["common"]
        common = original_import_common()
        common.DIAGNOSTIC_RUNNER = DIAGNOSTIC_RUNNER
        common.EXPECTED["diagnostic_runner"] = EXPECTED[DIAGNOSTIC_RUNNER]
        common.FORMAL_RUNNER = FORMAL_RUNNER
        common.EXPECTED["formal_runner"] = EXPECTED[FORMAL_RUNNER]
        original_run = common.run

        def bounded_run(argv: Any, log: Path, environment: Any) -> None:
            if str(EXPANDER) not in [str(value) for value in argv]:
                original_run(argv, log, environment)
                return
            try:
                completed = subprocess.run(
                    list(argv),
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=dict(environment),
                    timeout=1860,
                )
            except subprocess.TimeoutExpired as exc:
                common.atomic_text(log, str(exc.stdout or ""))
                raise RuntimeError("v41 expansion exceeded process timeout") from exc
            common.atomic_text(log, completed.stdout or "")
            if completed.returncode != 0:
                raise RuntimeError(f"v41 expansion failed rc={completed.returncode}")

        common.run = bounded_run
        v32.install_algorithm_hooks(v26, v27, v30, common)
        v33.install_prethird_hook(v26, v27)
        cache["common"] = common
        return common

    def authority(common: Any, accepted_before: int) -> tuple[int, Path, bool]:
        if initial_formal_run is not None:
            accepted_after, state, clean = v26.validate_formal_completion(
                initial_formal_run,
                accepted_before,
                allow_wait=False,
            )
            if clean:
                return accepted_after, state, True
        if accepted_before != 0:
            raise RuntimeError("v41 nonzero counter lacks a clean formal authority")
        return 0, anchor_state, True

    v26.import_common = import_common
    v26.resolve_initial_authority = authority
    v26.selection_paths = lambda root: selection_paths(
        original_selection_paths,
        root,
        V41_LOOP,
        executed_history,
    )
    return v26, import_common()


def run_adopted_formal(
    v26: Any,
    common: Any,
    selection: Path,
    run_root: Path,
    gate_path: Path,
    anchor_state: Path,
    environment: Mapping[str, str],
) -> tuple[Path, bool]:
    ADOPTED_FORMAL_ROOT.mkdir(mode=0o700)
    config = v26.dynamic_config(common, ADOPTED_FORMAL_ROOT, 0)
    formal_selection, formal_gate, formal_run = v26.promote_formal(
        common,
        ADOPTED_FORMAL_ROOT,
        selection,
        run_root,
        gate_path,
        0,
        config,
    )
    v26.run_formal(
        common,
        formal_selection,
        formal_gate,
        formal_run,
        config,
        anchor_state,
        environment,
    )
    _, _, clean = v26.validate_formal_completion(formal_run, 0, allow_wait=False)
    return formal_run, clean


def preflight(anchor_state: Path, anchor_sha: str) -> None:
    verify_static_dependencies()
    if not anchor_state.is_file() or sha256(anchor_state) != anchor_sha:
        raise RuntimeError("v41 anchor SHA mismatch")
    anchor = load(anchor_state)
    if not (
        int(anchor.get("accepted_count", -1)) == 0
        and anchor.get("status") == "complete"
        and anchor.get("baseline_restored") is True
        and int(anchor.get("technical_failure_count", -1)) == 0
    ):
        raise RuntimeError("v41 anchor is not clean accepted0 authority")
    if not V40_COUNTER.is_file() or V40_COUNTER.read_text().strip() != "0":
        raise RuntimeError("v40 accepted counter is not zero")
    if V41_ROOT.exists() or V41_COUNTER.exists():
        raise RuntimeError("v41 fresh root already exists")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-anchor-state", type=Path, required=True)
    parser.add_argument("--current-anchor-state-sha256", required=True)
    parser.add_argument("--wait-seconds", type=int, default=21600)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    anchor_state = args.current_anchor_state.resolve()
    preflight(anchor_state, args.current_anchor_state_sha256)
    if args.preflight_only:
        print("TPC-H v41 preformal handoff preflight passed", flush=True)
        return 0

    V41_ROOT.mkdir(mode=0o700, parents=True)
    V41_COUNTER.write_text("0\n", encoding="utf-8")
    handoff_state = V41_ROOT / "preformal_handoff_state.json"
    atomic_json(
        handoff_state,
        {
            "contract": "tpch_v41_preformal_handoff_v1",
            "status": "waiting_v40_final_diagnostic",
            "accepted_count": 0,
            "new_diagnostic_calls": 0,
            "new_physical_calls": 0,
        },
    )
    selection, run_root = wait_for_final_v40_diagnostic(args.wait_seconds)
    source_evidence = clean_diagnostic(selection, run_root)
    if source_evidence["state"].get("resume_state_sha256") != sha256(anchor_state):
        raise RuntimeError("v40 diagnostic is not bound to the accepted0 anchor")
    controller_state = load(V40_LOOP / "controller_state.json")
    if not (
        int(controller_state.get("accepted_count", -1)) == 0
        and sha256(Path(str(controller_state.get("resume_state"))))
        == sha256(anchor_state)
    ):
        raise RuntimeError("v40 controller authority does not match the anchor")
    for formal_state_path in V40_LOOP.glob("cycle_*/formal_run/formal_state.json"):
        if int(load(formal_state_path).get("physical_call_count", 0)) != 0:
            raise RuntimeError("v40 already started a formal physical replay")
    executed_history = executed_v40_selections(selection.parent)
    v26, common = configure_controller(
        anchor_state,
        selection,
        run_root,
        executed_history,
        None,
    )
    v27 = import_module("tpch_v41_adopt_v27", V27)
    v30 = import_module("tpch_v41_adopt_v30", V30)
    v32 = import_module("tpch_v41_adopt_v32", V32)
    environment = common.runtime_environment()
    resealed, resealed_run, gate_path = reseal_and_gate(
        v26,
        v27,
        v30,
        v32,
        common,
        selection,
        run_root,
        anchor_state,
        environment,
    )
    gate = load(gate_path)
    audit = {
        "contract": "tpch_v41_v40_diagnostic_reuse_audit_v1",
        "source_cycle": str(selection.parent),
        "source_selection_sha256": sha256(selection),
        "source_state_sha256": sha256(run_root / "diagnostic_state.json"),
        "source_summary_sha256": sha256(run_root / "diagnostic_summary.json"),
        "resealed_selection_sha256": sha256(resealed),
        "entry_gate_sha256": sha256(gate_path),
        "entry_gate_passed": gate.get("passed") is True,
        "accepted_count": 0,
        "new_diagnostic_calls": 0,
        "new_physical_calls": 0,
        "source_formal_state_mutated": False,
        "v40_executed_selections_added_to_history": [
            {"path": str(path), "sha256": sha256(path)}
            for path in executed_history
        ],
    }
    atomic_json(V41_ROOT / "v40_diagnostic_reuse_audit.json", audit)

    formal_run = None
    formal_clean = False
    if gate.get("passed") is True:
        formal_run, formal_clean = run_adopted_formal(
            v26,
            common,
            resealed,
            resealed_run,
            gate_path,
            anchor_state,
            environment,
        )
    atomic_json(
        handoff_state,
        {
            **audit,
            "status": "starting_autonomous_continuation",
            "adopted_formal_started": formal_run is not None,
            "adopted_formal_clean": formal_clean,
            "adopted_formal_run": str(formal_run) if formal_run else None,
            "accepted_count": int(V41_COUNTER.read_text().strip()),
        },
    )

    v26, _ = configure_controller(
        anchor_state,
        resealed,
        resealed_run,
        [*executed_history, resealed],
        formal_run if formal_clean else None,
    )
    sys.argv = [sys.argv[0]]
    return int(v26.main())


if __name__ == "__main__":
    raise SystemExit(main())
