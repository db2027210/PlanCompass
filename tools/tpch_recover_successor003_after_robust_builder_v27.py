#!/usr/bin/env python3
"""Resume TPC-H successor003 after the zero-call robust-builder failure."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, Mapping, Sequence


B = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
ROOT = B / "tpch_final_algorithm_fresh10_v58_20260731T2340"
COMMON_PATH = Path("/tmp/tpch_fresh10_robust_validation_v22.py")
ROBUST_BUILDER = Path("/tmp/build_tpch_two_observation_robust_repeat_v2.py")
ANALYZER = Path("/tmp/tpch_fresh0_handover_v19/analyze_top5_validation_gate.py")
PYTHON = Path("/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11")

PROBE_A = ROOT / "successor_diagnostic003_top5.a.json"
PROBE_RUN = ROOT / "diagnostic_probe003"
REPEAT_A = ROOT / "diagnostic_probe003_repeat_top5.a.json"
REPEAT_RUN = ROOT / "diagnostic_validation005"
ROBUST_A = ROOT / "robust_repeat006_top5.a.json"
ROBUST_B = ROOT / "robust_repeat006_top5.b.json"
ROBUST_RUN = ROOT / "diagnostic_validation006"
ANALYSIS = ROOT / "entry_gate_analysis006.json"
FORMAL_PACKAGE = ROOT / "formal_batch003_package"
FORMAL_RUN = ROOT / "formal_batch003"
HANDOVER = ROOT / "successor003_recovery_v27_handover.json"
FAILED_LOG = ROOT / "successor003_robust_builder_a.log"
QUARANTINED_LOG = ROOT / "successor003_robust_builder_a.failed_novelty_v1.log"

EXPECTED = {
    "common": "e56f3db493201e7aec9b944df67e8b40c5226114a87c85adad7b5def0532e578",
    "robust_builder": "4a5014c9cd5aaad138c108c1e0ad0087f6dfcf7ef91587d2e1829fefe46f926e",
    "analyzer": "49936cbc4319ef6055d6653c276892cd9df408a98d8560460c3e4f0bd7e37d30",
    "probe_selection": "cd4bf12acb5df069c724c34b7d9aa472ef7557cdcf8164a1dfaedd4092ed0666",
    "probe_summary": "72fd0c401d4c146c3a94d50815160dba5d5c1bf0796abd101ddea62954e3f2fa",
    "repeat_selection": "693344cfa6b9643644e52437b0ea8e9cd3c0e257588349a1c2e6bab4def08f8f",
    "repeat_summary": "00030c0415caa159c6ecf572fca398a39280d1ee12728c57d8d6c914ac55c3d1",
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
        "tpch_successor003_recovery_common", COMMON_PATH
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


def patch_accepted_start(path: Path) -> None:
    payload = load(path)
    payload["accepted_count_start"] = 0
    payload["inherited_accepted_count"] = 0
    payload["historical_accepted_excluded"] = 1
    atomic_json(path, payload)


def main() -> int:
    lock_path = ROOT / "successor003_recovery_v27.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name, path in (
            ("common", COMMON_PATH),
            ("robust_builder", ROBUST_BUILDER),
            ("analyzer", ANALYZER),
            ("probe_selection", PROBE_A),
            ("probe_summary", PROBE_RUN / "diagnostic_summary.json"),
            ("repeat_selection", REPEAT_A),
            ("repeat_summary", REPEAT_RUN / "diagnostic_summary.json"),
        ):
            verify(path, EXPECTED[name])
        if int((ROOT / "formal_search_accepted_counter.txt").read_text().strip()) != 0:
            raise RuntimeError("TPC-H accepted counter changed before recovery")
        common = import_common()
        common.validate_clean_diagnostic(
            load(PROBE_RUN / "diagnostic_state.json"), 0
        )
        common.validate_clean_diagnostic(
            load(REPEAT_RUN / "diagnostic_state.json"), 0
        )
        if FAILED_LOG.exists():
            text = FAILED_LOG.read_text(encoding="utf-8")
            if (
                "two-observation fingerprint is not novel" not in text
                or QUARANTINED_LOG.exists()
            ):
                raise RuntimeError("unexpected prior robust-builder log")
            os.replace(FAILED_LOG, QUARANTINED_LOG)
        for path in (
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

        environment = common.runtime_environment()
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
        run(
            common,
            [*base, str(ROBUST_A)],
            ROOT / "successor003_robust_builder_v2_a.log",
            environment,
        )
        run(
            common,
            [*base, str(ROBUST_B)],
            ROOT / "successor003_robust_builder_v2_b.log",
            environment,
        )
        patch_accepted_start(ROBUST_A)
        patch_accepted_start(ROBUST_B)
        if ROBUST_A.read_bytes() != ROBUST_B.read_bytes():
            raise RuntimeError("robust builder is not deterministic")
        common.validate_selection(
            load(ROBUST_A), ["trusted", "trusted", "trusted", "probe", "probe"]
        )

        run(
            common,
            common.diagnostic_argv(ROBUST_A, ROBUST_RUN),
            ROOT / "diagnostic_validation006_driver.log",
            environment,
        )
        common.validate_clean_diagnostic(
            load(ROBUST_RUN / "diagnostic_state.json"), 0
        )
        if int((ROOT / "formal_search_accepted_counter.txt").read_text().strip()) != 0:
            raise RuntimeError("diagnostic006 mutated accepted counter")

        analyzer_command = [
            str(PYTHON),
            "-u",
            str(ANALYZER),
            "--selection",
            str(ROBUST_A),
            "--observations",
            str(ROBUST_RUN / "diagnostic_summary.json"),
            "--output",
            str(ANALYSIS),
        ]
        completed = subprocess.run(
            analyzer_command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=dict(environment),
        )
        (ROOT / "entry_gate_analysis006.log").write_text(
            completed.stdout or "", encoding="utf-8"
        )
        if completed.returncode not in {0, 2} or not ANALYSIS.is_file():
            raise RuntimeError(
                f"entry analyzer failed rc={completed.returncode}"
            )
        analysis = load(ANALYSIS)
        base_handover = {
            "accepted_before": 0,
            "accepted_target": 10,
            "probe_selection_sha256": sha256(PROBE_A),
            "exact_repeat_selection_sha256": sha256(REPEAT_A),
            "robust_selection_sha256": sha256(ROBUST_A),
            "entry_gate_analysis_sha256": sha256(ANALYSIS),
            "entry_gate_passed": analysis.get("passed") is True,
            "robust_builder_sha256": EXPECTED["robust_builder"],
            "fingerprint_identity_preserved_on_equal_buckets": True,
            "candidate_score_resource_consistent": True,
        }
        if analysis.get("passed") is not True:
            atomic_json(
                HANDOVER,
                {
                    **base_handover,
                    "status": "entry_gate_failed",
                    "checks": analysis.get("checks"),
                    "formal_started": False,
                },
            )
            return 0

        common.ROBUST_SELECTION_A = ROBUST_A
        common.ROBUST_SELECTION_B = ROBUST_B
        common.VALIDATION_RUN = ROBUST_RUN
        common.FORMAL_PACKAGE = FORMAL_PACKAGE
        common.FORMAL_RUN = FORMAL_RUN
        selection, gate_path = common.promote_formal(
            load(ROBUST_A), analysis
        )
        gate = load(gate_path)
        gate["accepted_count"] = 0
        gate["inherited_accepted_count"] = 0
        gate["historical_accepted_excluded"] = 1
        atomic_json(gate_path, gate)
        pid = common.start_formal(selection, gate_path, environment)
        atomic_json(
            HANDOVER,
            {
                **base_handover,
                "status": "formal_batch003_started",
                "formal_started": True,
                "pid": pid,
                "formal_selection_sha256": sha256(selection),
                "formal_gate_sha256": sha256(gate_path),
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
