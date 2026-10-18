#!/usr/bin/env python3
"""Run the two-repetition ABC validation only after Algorithm C is terminal."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
CONTRACT = "algorithm_c_postterminal_abc_validation_handoff_v1"
MATCHED_CONTRACT = "algorithm_abc_pg_common_domain_final_validation_v1"


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_terminal_c(formal_root: Path) -> Dict[str, Any]:
    """Read only C's launcher state; do not load any A/B authority here."""
    launcher_path = formal_root / "launcher_status.json"
    if not launcher_path.is_file():
        raise RuntimeError("Algorithm C launcher status is missing")
    launcher = read_json(launcher_path)
    if launcher.get("phase") != "finished":
        raise RuntimeError("Algorithm C is not successfully terminal")
    returncodes = launcher.get("returncodes")
    if not isinstance(returncodes, Mapping) or set(returncodes) != set(SCENES):
        raise RuntimeError("Algorithm C terminal return-code coverage is incomplete")
    if any(returncodes.get(scene) != 0 for scene in SCENES):
        raise RuntimeError("Algorithm C has a nonzero terminal return code")
    if launcher.get("failures"):
        raise RuntimeError("Algorithm C launcher reports failures")
    if launcher.get("deadline_signals"):
        raise RuntimeError("Algorithm C launcher reports deadline signals")
    return launcher


def require_fresh_output_root(output_root: Path) -> None:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"output root is not empty; evidence will not be overwritten: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)


def run_logged(command: Sequence[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(
            list(command),
            check=False,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    return completed.returncode


def run_handoff(
    *,
    formal_root: Path,
    output_root: Path,
    seeds: Sequence[int],
    common_runner: Path,
    common_verifier: Path,
    builder: Path,
    terminal_auditor: Path,
    python_bin: Path,
) -> int:
    formal_root = formal_root.resolve()
    output_root = output_root.resolve()
    manifest_path = formal_root / "budgeted_launch_manifest.json"
    if manifest_path.is_file() and read_json(manifest_path).get(
        "postterminal_abc_validation_enabled"
    ) is False:
        raise RuntimeError("post-terminal validation is disabled for this search-only campaign")
    if len(seeds) != 2 or len(set(seeds)) != 2:
        raise RuntimeError("exactly two distinct validation seeds are required")

    tool_paths = {
        "common_runner": common_runner.resolve(),
        "common_verifier": common_verifier.resolve(),
        "matched_qps_builder": builder.resolve(),
        "terminal_auditor": terminal_auditor.resolve(),
    }
    missing_tools = [name for name, path in tool_paths.items() if not path.is_file()]
    if missing_tools:
        raise RuntimeError(f"post-terminal validation tools are missing: {missing_tools}")

    # This gate must precede every subprocess that can resolve A/B authorities.
    launcher = require_terminal_c(formal_root)
    require_fresh_output_root(output_root)
    receipt_path = output_root / "handoff_receipt.json"
    receipt: Dict[str, Any] = {
        "contract": CONTRACT,
        "phase": "running",
        "algorithm_c_formal_root": str(formal_root),
        "algorithm_c_launcher_finished_at_epoch_s": launcher.get("finished_at_epoch_s"),
        "algorithm_c_terminal_gate_passed_before_a_b_load": True,
        "seeds": list(seeds),
        "python_bin": str(python_bin.resolve()),
        "tool_sources": {
            name: {"path": str(path), "sha256": sha256(path)}
            for name, path in tool_paths.items()
        },
        "steps": [],
        "started_at_epoch_s": time.time(),
    }
    write_json(receipt_path, receipt)

    def execute(name: str, command: Sequence[str], accepted_returncodes: Iterable[int]) -> int:
        log_path = output_root / "logs" / f"{name}.log"
        returncode = run_logged(command, log_path)
        step = {
            "name": name,
            "command": list(command),
            "returncode": returncode,
            "accepted_returncodes": list(accepted_returncodes),
            "log_path": str(log_path),
            "log_sha256": sha256(log_path),
        }
        receipt["steps"].append(step)
        write_json(receipt_path, receipt)
        if returncode not in step["accepted_returncodes"]:
            raise RuntimeError(f"{name} failed with return code {returncode}")
        return returncode

    try:
        preaudit_path = output_root / "algorithm_c_terminal_preaudit.json"
        execute(
            "algorithm_c_terminal_preaudit",
            (
                str(python_bin), "-B", str(terminal_auditor),
                "--campaign-root", str(formal_root),
                "--output", str(preaudit_path),
            ),
            (0, 1),
        )
        preaudit = read_json(preaudit_path)
        if preaudit.get("complete") is not True:
            raise RuntimeError("Algorithm C terminal preaudit is incomplete")
        runtime = preaudit.get("runtime_independence") or {}
        coverage = preaudit.get("coverage_summary") or {}
        if runtime.get("external_baseline_independence_passed") is not True:
            raise RuntimeError("Algorithm C A/B runtime-independence audit failed")
        if runtime.get("sealed_runtime_integrity_passed") is not True:
            raise RuntimeError("Algorithm C sealed runtime integrity audit failed")
        if coverage.get("terminal_evidence_integrity_passed") is not True:
            raise RuntimeError("Algorithm C terminal evidence integrity audit failed")

        repetition_roots: List[Path] = []
        for repetition, seed in enumerate(seeds, start=1):
            repetition_root = output_root / f"abc_rep{repetition}_seed{seed}"
            repetition_roots.append(repetition_root)
            execute(
                f"abc_rep{repetition}_runner",
                (
                    str(python_bin), "-B", "-u", str(common_runner),
                    "--output-dir", str(repetition_root),
                    "--seed", str(seed),
                    "--max-stage-attempts", "3",
                    "--maximum-baseline-drift-pct", "10",
                    "--methods", "algorithm_a", "algorithm_b", "algorithm_c",
                    "--algorithm-c-formal-root", str(formal_root),
                ),
                (0,),
            )
            verification_path = output_root / f"abc_rep{repetition}_verification.json"
            execute(
                f"abc_rep{repetition}_verifier",
                (
                    str(python_bin), "-B", str(common_verifier),
                    str(repetition_root), "--output", str(verification_path),
                ),
                (0,),
            )
            if read_json(verification_path).get("passed") is not True:
                raise RuntimeError(f"ABC repetition {repetition} verification failed")

        matched_path = output_root / "algorithm_abc_matched_absolute_qps.json"
        builder_command: List[str] = [str(python_bin), "-B", str(builder)]
        for repetition_root in repetition_roots:
            builder_command.extend(("--run-root", str(repetition_root)))
        builder_command.extend(("--output", str(matched_path)))
        execute("matched_qps_builder", builder_command, (0,))
        if read_json(matched_path).get("contract") != MATCHED_CONTRACT:
            raise RuntimeError("matched absolute-QPS contract mismatch")

        final_audit_path = output_root / "algorithm_c_terminal_audit_with_matched_qps.json"
        final_returncode = execute(
            "algorithm_c_final_publication_audit",
            (
                str(python_bin), "-B", str(terminal_auditor),
                "--campaign-root", str(formal_root),
                "--matched-absolute-qps", str(matched_path),
                "--output", str(final_audit_path),
            ),
            (0, 1),
        )
        final_audit = read_json(final_audit_path)
        target_met = bool(
            final_returncode == 0
            and final_audit.get("complete") is True
            and final_audit.get("publication_claim_allowed") is True
            and final_audit.get(
                "publication_absolute_qps_and_efficiency_four_scene_target_met"
            ) is True
        )
        receipt.update({
            "phase": "complete" if target_met else "complete_without_four_scene_win",
            "publication_target_met": target_met,
            "matched_absolute_qps_path": str(matched_path),
            "final_audit_path": str(final_audit_path),
            "completed_at_epoch_s": time.time(),
        })
        write_json(receipt_path, receipt)
        return 0 if target_met else 1
    except Exception as exc:
        receipt.update({
            "phase": "failed_closed",
            "error": str(exc),
            "failed_at_epoch_s": time.time(),
            "evidence_preserved": True,
            "automatic_retry": False,
        })
        write_json(receipt_path, receipt)
        raise


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--algorithm-c-formal-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--seed", type=int, action="append", required=True)
    parser.add_argument(
        "--common-runner",
        type=Path,
        default=ROOT / "shell" / "run_pg_common_domain_frozen_champions.py",
    )
    parser.add_argument(
        "--common-verifier",
        type=Path,
        default=ROOT / "shell" / "verify_pg_common_domain_frozen_champions.py",
    )
    parser.add_argument(
        "--builder",
        type=Path,
        default=ROOT / "shell" / "build_pg_algorithm_abc_matched_absolute_qps.py",
    )
    parser.add_argument(
        "--terminal-auditor",
        type=Path,
        default=ROOT / "shell" / "audit_pg_algorithm_c_adaptive_formal.py",
    )
    parser.add_argument("--python-bin", type=Path, default=Path(sys.executable))
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run_handoff(
            formal_root=args.algorithm_c_formal_root,
            output_root=args.output_root,
            seeds=args.seed,
            common_runner=args.common_runner,
            common_verifier=args.common_verifier,
            builder=args.builder,
            terminal_auditor=args.terminal_auditor,
            python_bin=args.python_bin,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
