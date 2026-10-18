#!/usr/bin/env python3
"""Continue TPC-H autonomously after formal batch002.

The controller runs on node01 and never opens SSH.  It waits for the current
formal batch to finish, builds a deterministic novel diagnostic Top-5, performs
an exact-point repeat and a two-observation robust repeat, and starts formal
batch003 only when the fixed entry gate passes.  Every diagnostic batch is
state-neutral and capped at five physical workload calls.
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
from typing import Any, Dict, Mapping, Sequence


B = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
ROOT = B / "tpch_final_algorithm_fresh10_v58_20260731T2340"
COMMON_PATH = Path("/tmp/tpch_fresh10_robust_validation_v22.py")
SUCCESSOR_BUILDER = Path(
    "/tmp/build_tpch_autonomous_successor_top5_v3_interior_audit.py"
)
EXACT_REPEAT_BUILDER = Path(
    "/tmp/tpch_fresh0_handover_v19/build_tpch_exact_plan_repeat_validation.py"
)
ROBUST_BUILDER = Path("/tmp/build_tpch_two_observation_robust_repeat_v1.py")
ANALYZER = Path("/tmp/tpch_fresh0_handover_v19/analyze_top5_validation_gate.py")
PYTHON = Path("/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11")

FORMAL_PREVIOUS = ROOT / "formal_batch002"
PREVIOUS_DIAGNOSTIC_SELECTION = ROOT / "robust_repeat004_top5.a.json"
PREVIOUS_DIAGNOSTIC_RUN = ROOT / "diagnostic_validation004"
OLD_SUCCESSOR_SELECTION = ROOT / "successor_diagnostic002_top5_v1.json"
EXPANSION = (
    B
    / "gradient_debug_round57d_multi_neighbor_expansion_v17_20260731T2300"
    / "expanded_candidates.json"
)

PROBE_A = ROOT / "successor_diagnostic003_top5.a.json"
PROBE_B = ROOT / "successor_diagnostic003_top5.b.json"
PROBE_RUN = ROOT / "diagnostic_probe003"
REPEAT_A = ROOT / "diagnostic_probe003_repeat_top5.a.json"
REPEAT_B = ROOT / "diagnostic_probe003_repeat_top5.b.json"
REPEAT_RUN = ROOT / "diagnostic_validation005"
ROBUST_A = ROOT / "robust_repeat006_top5.a.json"
ROBUST_B = ROOT / "robust_repeat006_top5.b.json"
ROBUST_RUN = ROOT / "diagnostic_validation006"
ANALYSIS = ROOT / "entry_gate_analysis006.json"
FORMAL_PACKAGE = ROOT / "formal_batch003_package"
FORMAL_RUN = ROOT / "formal_batch003"
HANDOVER = ROOT / "successor003_autonomous_v23_handover.json"

EXPECTED = {
    "common": "e56f3db493201e7aec9b944df67e8b40c5226114a87c85adad7b5def0532e578",
    "successor_builder": "480074531063b147ff827e7ee8742d4cd5d3f7bd787e6c6ec4eb0bd1e773fba6",
    "exact_repeat_builder": "4f1684e73c9c91f32d94655053a4c6af5a8f8f0d2d2eb8a94363eaa24b48cfe7",
    "robust_builder": "eaf110b3830f928cc44ff0ccc198badf3c6d1adad77c7aaa796f83a67847824b",
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
    specification = importlib.util.spec_from_file_location("tpch_v22_common", COMMON_PATH)
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load common controller")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def run(common: Any, argv: Sequence[str], log: Path, environment: Mapping[str, str]) -> None:
    common.run(argv, log, environment)


def wait_previous_formal() -> int:
    deadline = time.monotonic() + 6 * 60 * 60
    state_path = FORMAL_PREVIOUS / "formal_state.json"
    summary_path = FORMAL_PREVIOUS / "summary.json"
    while time.monotonic() < deadline:
        if state_path.is_file():
            state = load(state_path)
            status = state.get("status")
            if status == "complete":
                if not summary_path.is_file():
                    raise RuntimeError("completed formal batch lacks summary")
                summary = load(summary_path)
                checks = (
                    int(state.get("physical_call_count", -1)) == 5,
                    int(state.get("valid_replay_count", -1)) == 5,
                    int(state.get("technical_failure_count", -1)) == 0,
                    state.get("baseline_restored") is True,
                    int(summary.get("physical_call_count", -1)) == 5,
                    int(summary.get("valid_replay_count", -1)) == 5,
                    int(summary.get("technical_failure_count", -1)) == 0,
                    summary.get("baseline_restored") is True,
                )
                if not all(checks):
                    raise RuntimeError("formal batch002 is not clean phys5/valid5")
                accepted = int(state.get("accepted_count", -1))
                counter = int(
                    (ROOT / "formal_search_accepted_counter.txt")
                    .read_text(encoding="utf-8")
                    .strip()
                )
                if accepted < 0 or accepted != counter:
                    raise RuntimeError("formal accepted counter mismatch")
                return accepted
            if status not in {"running", None}:
                raise RuntimeError(f"formal batch002 ended with status={status}")
        time.sleep(15)
    raise RuntimeError("timed out waiting for formal batch002")


def exclusion_paths() -> list[Path]:
    payload = load(OLD_SUCCESSOR_SELECTION)
    evidence = payload.get("source_evidence") or {}
    rows = evidence.get("additional_exclusions") or []
    paths = [Path(str(row["path"])) for row in rows]
    paths.extend(
        [
            OLD_SUCCESSOR_SELECTION,
            ROOT / "diagnostic_probe002_repeat_top5.a.json",
            ROOT / "formal_batch001_package/top5_formal.json",
            ROOT / "formal_batch002_package/top5_formal.json",
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = str(path)
        if resolved not in seen:
            unique.append(path)
            seen.add(resolved)
    return unique


def patch_accepted_start(path: Path, accepted: int) -> None:
    payload = load(path)
    payload["accepted_count_start"] = accepted
    payload["inherited_accepted_count"] = 0
    payload["historical_accepted_excluded"] = 1
    atomic_json(path, payload)


def build_successor(common: Any, accepted: int, environment: Mapping[str, str]) -> None:
    base = [
        str(PYTHON),
        "-u",
        str(SUCCESSOR_BUILDER),
        "--previous-selection",
        str(PREVIOUS_DIAGNOSTIC_SELECTION),
        "--previous-summary",
        str(PREVIOUS_DIAGNOSTIC_RUN / "diagnostic_summary.json"),
        "--expanded-candidates",
        str(EXPANSION),
        "--minimum-score-span",
        "0.002",
    ]
    for path in exclusion_paths():
        base.extend(["--additional-exclusion-selection", str(path)])
    run(common, [*base, "--output", str(PROBE_A)], ROOT / "successor003_builder_a.log", environment)
    run(common, [*base, "--output", str(PROBE_B)], ROOT / "successor003_builder_b.log", environment)
    patch_accepted_start(PROBE_A, accepted)
    patch_accepted_start(PROBE_B, accepted)
    if PROBE_A.read_bytes() != PROBE_B.read_bytes():
        raise RuntimeError("successor builder is not deterministic")
    roles = [str(row.get("selection_role")) for row in load(PROBE_A).get("top5") or []]
    if roles not in (["probe"] * 5, ["trusted", "trusted", "trusted", "probe", "probe"]):
        raise RuntimeError(f"invalid successor roles: {roles}")


def build_exact_repeat(common: Any, accepted: int, environment: Mapping[str, str]) -> None:
    base = [
        str(PYTHON),
        "-u",
        str(EXACT_REPEAT_BUILDER),
        "--selection",
        str(PROBE_A),
        "--summary",
        str(PROBE_RUN / "diagnostic_summary.json"),
        "--minimum-score-span",
        "0.002",
        "--output",
    ]
    run(common, [*base, str(REPEAT_A)], ROOT / "successor003_repeat_builder_a.log", environment)
    run(common, [*base, str(REPEAT_B)], ROOT / "successor003_repeat_builder_b.log", environment)
    patch_accepted_start(REPEAT_A, accepted)
    patch_accepted_start(REPEAT_B, accepted)
    if REPEAT_A.read_bytes() != REPEAT_B.read_bytes():
        raise RuntimeError("exact repeat builder is not deterministic")
    common.validate_selection(load(REPEAT_A), ["trusted", "trusted", "trusted", "probe", "probe"])


def build_robust_repeat(common: Any, accepted: int, environment: Mapping[str, str]) -> None:
    base = [
        str(PYTHON),
        "-u",
        str(ROBUST_BUILDER),
        "--source-selection",
        str(PROBE_A),
        "--source-summary",
        str(PROBE_RUN / "diagnostic_summary.json"),
        "--repeat-selection",
        str(REPEAT_A),
        "--repeat-summary",
        str(REPEAT_RUN / "diagnostic_summary.json"),
        "--maximum-trusted-score-span",
        "0.01",
        "--maximum-trusted-resource-factor",
        "1.5",
        "--output",
    ]
    run(common, [*base, str(ROBUST_A)], ROOT / "successor003_robust_builder_a.log", environment)
    run(common, [*base, str(ROBUST_B)], ROOT / "successor003_robust_builder_b.log", environment)
    patch_accepted_start(ROBUST_A, accepted)
    patch_accepted_start(ROBUST_B, accepted)
    if ROBUST_A.read_bytes() != ROBUST_B.read_bytes():
        raise RuntimeError("robust repeat builder is not deterministic")
    common.validate_selection(load(ROBUST_A), ["trusted", "trusted", "trusted", "probe", "probe"])


def configure_common(common: Any) -> None:
    common.ROBUST_SELECTION_A = ROBUST_A
    common.ROBUST_SELECTION_B = ROBUST_B
    common.VALIDATION_RUN = ROBUST_RUN
    common.FORMAL_PACKAGE = FORMAL_PACKAGE
    common.FORMAL_RUN = FORMAL_RUN


def main() -> int:
    lock_path = ROOT / "successor003_autonomous_v23.lock"
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
        for path in (
            PROBE_A,
            PROBE_B,
            PROBE_RUN,
            REPEAT_A,
            REPEAT_B,
            REPEAT_RUN,
            ROBUST_A,
            ROBUST_B,
            ROBUST_RUN,
            ANALYSIS,
            FORMAL_PACKAGE,
            FORMAL_RUN,
            HANDOVER,
        ):
            if path.exists():
                raise RuntimeError(f"refusing to reuse output: {path}")

        common = import_common()
        environment = common.runtime_environment()
        accepted = wait_previous_formal()
        build_successor(common, accepted, environment)

        run(
            common,
            common.diagnostic_argv(PROBE_A, PROBE_RUN),
            ROOT / "diagnostic_probe003_driver.log",
            environment,
        )
        common.validate_clean_diagnostic(load(PROBE_RUN / "diagnostic_state.json"), accepted)
        build_exact_repeat(common, accepted, environment)

        run(
            common,
            common.diagnostic_argv(REPEAT_A, REPEAT_RUN),
            ROOT / "diagnostic_validation005_driver.log",
            environment,
        )
        common.validate_clean_diagnostic(load(REPEAT_RUN / "diagnostic_state.json"), accepted)
        build_robust_repeat(common, accepted, environment)

        run(
            common,
            common.diagnostic_argv(ROBUST_A, ROBUST_RUN),
            ROOT / "diagnostic_validation006_driver.log",
            environment,
        )
        common.validate_clean_diagnostic(load(ROBUST_RUN / "diagnostic_state.json"), accepted)
        if int((ROOT / "formal_search_accepted_counter.txt").read_text().strip()) != accepted:
            raise RuntimeError("diagnostics mutated accepted counter")

        run(
            common,
            [
                str(PYTHON),
                "-u",
                str(ANALYZER),
                "--selection",
                str(ROBUST_A),
                "--observations",
                str(ROBUST_RUN / "diagnostic_summary.json"),
                "--output",
                str(ANALYSIS),
            ],
            ROOT / "entry_gate_analysis006.log",
            environment,
        )
        analysis = load(ANALYSIS)
        if analysis.get("passed") is not True:
            raise RuntimeError(f"validation gate failed: {analysis.get('checks')}")

        configure_common(common)
        repeat = load(ROBUST_A)
        selection, gate_path = common.promote_formal(repeat, analysis)
        gate = load(gate_path)
        gate["accepted_count"] = accepted
        gate["inherited_accepted_count"] = 0
        gate["historical_accepted_excluded"] = 1
        atomic_json(gate_path, gate)
        pid = common.start_formal(selection, gate_path, environment)
        atomic_json(
            HANDOVER,
            {
                "status": "formal_batch003_started",
                "pid": pid,
                "accepted_before": accepted,
                "accepted_target": 10,
                "probe_selection_sha256": sha256(PROBE_A),
                "exact_repeat_selection_sha256": sha256(REPEAT_A),
                "robust_selection_sha256": sha256(ROBUST_A),
                "entry_gate_analysis_sha256": sha256(ANALYSIS),
                "formal_selection_sha256": sha256(selection),
                "formal_gate_sha256": sha256(gate_path),
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
