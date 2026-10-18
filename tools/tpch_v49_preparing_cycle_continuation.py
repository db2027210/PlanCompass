#!/usr/bin/env python3
"""Resume a clean TPC-H v48 ``preparing_cycle`` boundary.

This adapter is intentionally narrow.  It only accepts the state written
after v48 absorbed the previous cycle's rank-4 probe and before the next
cycle started a diagnostic workload.  It neither selects candidates nor
changes any quality threshold; candidate generation remains in v48.
"""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import inspect
import json
from pathlib import Path
import re
import textwrap
from typing import Any, Dict, Mapping


V48 = Path("/tmp/tpch_v48_prethird_feasibility_binding.py")
EXPECTED_V48_SHA256 = (
    "5bf7ff767338860f3e86576c9ef7e5db5b4ce60d111397e89f15776311397458"
)
CONTRACT = "tpch_v49_clean_preparing_cycle_continuation_v1"

ALLOWED_ROOT_FILES = {
    "config.a.json",
    "config.b.json",
    "probe_top5.a.json",
    "probe_top5.b.json",
    "probe_builder_a.log",
    "probe_builder_b.log",
}
ALLOWED_PREFIXES = ("dynamic_expansion_v32/",)
RUN_LOOP_MKDIR_NEEDLE = (
    "exist_ok=(resume_existing_cycle004 and cycle == 4)"
)
RUN_LOOP_MKDIR_REPLACEMENT = (
    "exist_ok=(resume_existing_cycle004 and cycle == start_cycle)"
)


def import_v48() -> Any:
    specification = importlib.util.spec_from_file_location("tpch_v49_v48", V48)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import v48 controller: {V48}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    if module.sha256(V48) != EXPECTED_V48_SHA256:
        raise RuntimeError("v48 controller SHA mismatch")
    return module


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def generalized_existing_cycle_run_loop(v48: Any) -> Any:
    """Generalize v48's SHA-bound cycle-4 directory reuse to ``start_cycle``."""

    source = textwrap.dedent(inspect.getsource(v48.run_loop))
    if source.count(RUN_LOOP_MKDIR_NEEDLE) != 1:
        raise RuntimeError("v48 run_loop existing-cycle guard shape changed")
    transformed = source.replace(
        RUN_LOOP_MKDIR_NEEDLE,
        RUN_LOOP_MKDIR_REPLACEMENT,
    )
    namespace = dict(v48.__dict__)
    exec(compile(transformed, str(V48), "exec"), namespace)
    return namespace["run_loop"]


def _continuation_log(log: Path) -> Path:
    suffix = log.suffix or ".log"
    stem = log.name[: -len(suffix)] if log.suffix else log.name
    for index in range(1, 100):
        candidate = log.with_name(
            f"{stem}.v49_preparing_cycle_resume_{index:02d}{suffix}"
        )
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"continuation log namespace exhausted: {log}")


def _allowed_preworkload_file(relative: str) -> bool:
    return bool(
        relative in ALLOWED_ROOT_FILES
        or relative.startswith(ALLOWED_PREFIXES)
        or re.fullmatch(
            r"probe_builder_[ab]\.v49_preparing_cycle_resume_[0-9]{2}\.log",
            relative,
        )
    )


def validate_boundary(
    v48: Any,
    v41: Any,
    paths: Mapping[str, Path],
    *,
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
    expected_cycle: int,
) -> Dict[str, Any]:
    state_path = paths["state"]
    if not state_path.is_file() or v48.sha256(state_path) != expected_state_sha:
        raise RuntimeError("preparing-cycle controller_state SHA mismatch")
    state = v48.load(state_path)
    cycles = state.get("cycles") or []
    last_cycle = cycles[-1] if cycles else {}
    absorption = last_cycle.get("v47_rank4_probe_absorption") or {}
    accepted = int(state.get("accepted_count", -1))
    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v26"
        and state.get("status") == "preparing_cycle"
        and accepted == 1
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_cycle", -1)) == expected_cycle
        and int(last_cycle.get("cycle", -1)) == expected_cycle - 1
        and int(last_cycle.get("accepted_before", -1)) == accepted
        and int(last_cycle.get("accepted_after", -1)) == accepted
        and last_cycle.get("formal_started") is False
        and last_cycle.get("entry_gate_passed") is False
        and absorption.get("contract") == v48.RANK_MISS_ABSORPTION_CONTRACT
        and absorption.get("status") == "automatic_bounded_reexploration_ready"
        and absorption.get("probe_absorbed_as_trusted") is False
        and absorption.get("manual_candidate_injection") is False
        and int(absorption.get("new_workload_calls", -1)) == 0
    ):
        raise RuntimeError("controller is not at the clean v48 preparing-cycle boundary")

    resume_state = Path(str(state.get("resume_state") or ""))
    v48.validate_counter(paths["counter"], expected_counter_sha)
    v48.validate_formal_authority(resume_state, expected_formal_sha)

    evidence_selection = Path(
        str(absorption.get("effective_reexploration_selection") or "")
    )
    evidence_run = Path(str(absorption.get("effective_reexploration_run") or ""))
    previous_cycle_root = v41.V41_LOOP / f"cycle_{expected_cycle - 1:03d}"
    expected_evidence_sha = str(
        absorption.get("effective_reexploration_selection_sha256") or ""
    )
    if not (
        evidence_selection.is_file()
        and evidence_run.is_dir()
        and _within(evidence_selection, previous_cycle_root)
        and _within(evidence_run, previous_cycle_root)
        and v48.sha256(evidence_selection) == expected_evidence_sha
        and last_cycle.get("next_reexploration_selection_sha256")
        == expected_evidence_sha
        and (evidence_run / "diagnostic_summary.json").is_file()
        and (evidence_run / "diagnostic_state.json").is_file()
    ):
        raise RuntimeError("previous-cycle reexploration evidence binding mismatch")
    evidence_payload = v48.load(evidence_selection)
    builder_selection = Path(
        str(evidence_payload.get("source_predicted_selection") or "")
    )
    builder_summary = Path(
        str(evidence_payload.get("source_realized_summary") or "")
    )
    builder_selection_sha = str(
        evidence_payload.get("source_predicted_selection_sha256") or ""
    )
    builder_summary_sha = str(
        evidence_payload.get("source_realized_summary_sha256") or ""
    )
    if not (
        evidence_payload.get("schema_version") == v48.REALIZED_SELECTION_CONTRACT
        and evidence_payload.get("selection_contract")
        == v48.REALIZED_SELECTION_CONTRACT
        and evidence_payload.get("manual_candidate_injection") is False
        and int(evidence_payload.get("new_workload_calls", -1)) == 0
        and builder_selection.is_file()
        and builder_summary.is_file()
        and _within(builder_selection, previous_cycle_root)
        and _within(builder_summary, previous_cycle_root)
        and builder_summary.parent.resolve() == evidence_run.resolve()
        and v48.sha256(builder_selection) == builder_selection_sha
        and v48.sha256(builder_summary) == builder_summary_sha
        and v48.load(builder_summary).get("selection_sha256")
        == builder_selection_sha
    ):
        raise RuntimeError("realized evidence source selection/summary binding mismatch")

    cycle_root = v41.V41_LOOP / f"cycle_{expected_cycle:03d}"
    if not cycle_root.is_dir():
        raise RuntimeError("preparing-cycle directory is missing")
    v48.ensure_no_formal_started(cycle_root)
    forbidden = []
    unexpected = []
    observed_files = []
    for path in cycle_root.rglob("*"):
        relative = str(path.relative_to(cycle_root))
        if path.is_dir() and (
            path.name.startswith("attempt_")
            or path.name.startswith("diagnostic_")
            or path.name.startswith("formal_")
        ):
            forbidden.append(relative)
        if path.is_file():
            observed_files.append(relative)
            if not _allowed_preworkload_file(relative):
                unexpected.append(relative)
    if forbidden or unexpected:
        raise RuntimeError(
            "cycle is not a zero-workload preparing boundary: "
            f"forbidden={sorted(forbidden)} unexpected={sorted(unexpected)}"
        )
    for first_name, second_name, label in (
        ("config.a.json", "config.b.json", "config"),
        ("probe_top5.a.json", "probe_top5.b.json", "probe selection"),
    ):
        first = cycle_root / first_name
        second = cycle_root / second_name
        if first.exists() != second.exists():
            raise RuntimeError(f"partial deterministic {label} pair")
        if first.is_file() and first.read_bytes() != second.read_bytes():
            raise RuntimeError(f"deterministic {label} pair mismatch")

    config = cycle_root / "config.a.json"
    probe = cycle_root / "probe_top5.a.json"
    expansion_root = cycle_root / "dynamic_expansion_v32"
    expansion_outputs = (
        expansion_root / "expanded_candidates.a.json",
        expansion_root / "expanded_candidates.b.json",
        expansion_root / "audit.json",
    )
    expansion_complete = all(path.is_file() for path in expansion_outputs)
    if any(path.exists() for path in expansion_outputs) and not expansion_complete:
        raise RuntimeError("partial expansion output set cannot be resumed implicitly")
    if observed_files and not config.is_file():
        raise RuntimeError("preworkload artifacts exist without deterministic config")
    if probe.is_file() and not expansion_complete:
        raise RuntimeError("probe selection exists without completed expansion")

    protected_logs = [
        path
        for path in (
            cycle_root / "probe_builder_a.log",
            cycle_root / "probe_builder_b.log",
            expansion_root / "expand_a.log",
            expansion_root / "expand_b.log",
        )
        if path.is_file()
    ]

    return {
        "state": state,
        "cycle_root": cycle_root,
        "evidence_selection": evidence_selection,
        "evidence_run": evidence_run,
        "builder_selection": builder_selection,
        "builder_summary": builder_summary,
        "existing_config": config if config.is_file() else None,
        "existing_probe": probe if probe.is_file() else None,
        "expansion_complete": expansion_complete,
        "protected_logs": protected_logs,
        "preflight": {
            "contract": CONTRACT,
            "status": "ready_to_continue_clean_preparing_cycle",
            "cycle": expected_cycle,
            "accepted_count": accepted,
            "controller_state_sha256": expected_state_sha,
            "formal_state_sha256": expected_formal_sha,
            "accepted_counter_sha256": expected_counter_sha,
            "evidence_selection": str(evidence_selection.resolve()),
            "evidence_selection_sha256": expected_evidence_sha,
            "builder_selection": str(builder_selection.resolve()),
            "builder_selection_sha256": builder_selection_sha,
            "builder_summary": str(builder_summary.resolve()),
            "builder_summary_sha256": builder_summary_sha,
            "observed_preworkload_files": sorted(observed_files),
            "existing_config_reused": config.is_file(),
            "existing_probe_reused": probe.is_file(),
            "completed_expansion_reused": expansion_complete,
            "physical_explain_calls_repeated": 0,
            "protected_logs": [str(path.resolve()) for path in protected_logs],
            "new_workload_calls": 0,
            "formal_state_mutated": False,
            "accepted_counter_mutated": False,
            "manual_candidate_injection": False,
            "thresholds_relaxed": False,
            "next_action": "resume_v48_run_loop_at_existing_cycle",
        },
    }


def resume_boundary(
    v48: Any,
    v41: Any,
    paths: Mapping[str, Path],
    boundary: Mapping[str, Any],
) -> int:
    cycle_root = Path(str(boundary["cycle_root"]))
    existing_config = boundary.get("existing_config")
    evidence_selection = Path(str(boundary["evidence_selection"])).resolve()
    builder_selection = Path(str(boundary["builder_selection"])).resolve()
    builder_summary = Path(str(boundary["builder_summary"])).resolve()
    protected_logs = {
        Path(str(path)).resolve() for path in boundary.get("protected_logs") or []
    }
    original_configure = v41.configure_controller

    def configure_with_preworkload_reuse(*args: Any, **kwargs: Any) -> Any:
        v26, common = original_configure(*args, **kwargs)
        original_dynamic_config = v26.dynamic_config
        original_run = v26.run

        def dynamic_config_reuse(*config_args: Any, **config_kwargs: Any) -> Path:
            requested_root = Path(str(config_args[1])).resolve()
            if requested_root == cycle_root.resolve() and existing_config is not None:
                return Path(str(existing_config))
            return original_dynamic_config(*config_args, **config_kwargs)

        def run_preserving_logs(
            common_module: Any,
            argv: Any,
            log: Path,
            environment: Mapping[str, str],
        ) -> Any:
            command = [str(item) for item in argv]
            if "--previous-selection" in command:
                selection_index = command.index("--previous-selection") + 1
                summary_index = command.index("--previous-summary") + 1
                if Path(command[selection_index]).resolve() != evidence_selection:
                    raise RuntimeError("selector previous-selection input changed")
                command[selection_index] = str(builder_selection)
                command[summary_index] = str(builder_summary)
                rewritten = []
                index = 0
                while index < len(command):
                    if (
                        command[index] == "--additional-exclusion-selection"
                        and index + 1 < len(command)
                        and Path(command[index + 1]).resolve() == builder_selection
                    ):
                        index += 2
                        continue
                    rewritten.append(command[index])
                    index += 1
                if str(evidence_selection) not in rewritten:
                    rewritten.extend(
                        ["--additional-exclusion-selection", str(evidence_selection)]
                    )
                command = rewritten
            resolved_log = Path(log).resolve()
            destination = Path(log)
            if resolved_log in protected_logs:
                destination = _continuation_log(Path(log))
                if destination.exists():
                    raise RuntimeError(
                        f"continuation log target already exists: {destination}"
                    )
            return original_run(common_module, command, destination, environment)

        v26.dynamic_config = dynamic_config_reuse
        v26.run = run_preserving_logs
        return v26, common

    run_loop = generalized_existing_cycle_run_loop(v48)
    v41.configure_controller = configure_with_preworkload_reuse
    try:
        return run_loop(
            v41,
            paths,
            dict(boundary["state"]),
            resume_existing_cycle004=True,
            start_cycle=int(boundary["preflight"]["cycle"]),
            previous_selection_override=Path(str(boundary["evidence_selection"])),
            previous_run_override=Path(str(boundary["evidence_run"])),
        )
    finally:
        v41.configure_controller = original_configure


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--resume", action="store_true")
    parser.add_argument("--expected-controller-state-sha256", required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--expected-counter-sha256", required=True)
    parser.add_argument("--expected-cycle", type=int, required=True)
    args = parser.parse_args()

    v48 = import_v48()
    v41 = v48.verified_v41()
    paths = v48.current_paths(v41)
    paths["lock"].parent.mkdir(parents=True, exist_ok=True)
    with paths["lock"].open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        boundary = validate_boundary(
            v48,
            v41,
            paths,
            expected_state_sha=args.expected_controller_state_sha256,
            expected_formal_sha=args.expected_formal_state_sha256,
            expected_counter_sha=args.expected_counter_sha256,
            expected_cycle=args.expected_cycle,
        )
        if args.preflight_only:
            print(json.dumps(boundary["preflight"], sort_keys=True), flush=True)
            return 0
        return resume_boundary(v48, v41, paths, boundary)


if __name__ == "__main__":
    raise SystemExit(main())
