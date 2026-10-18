#!/usr/bin/env python3
"""Wait for the C handoff and start formal Algorithm B at a clean boundary."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rebind_mysql_algorithm_c_empty_replay_quarantine_v2 as quarantine_rebind


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
LAUNCHER = ROOT / "scripts/launch_mysql_four_scene_algorithm_b_from_c_v1.py"
PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/v18_formal_bounded_prestate_v10_20260814T1000p0800_preflight_attempt_0001.json"
PREPARED = ROOT / "history/mysql_four_scene_20260809/v18_formal_bounded_prestate_v10_20260814T1000p0800_rebind/prepared_binding.json"
ACCOUNTS = ROOT / "history/mysql_four_scene_20260809/v18_formal_bounded_prestate_v10_20260814T1000p0800_accounts/benchmark_accounts_binding.json"
R0 = ROOT / "history/mysql_four_scene_20260809/v18_formal_bounded_prestate_v10_20260814T1000p0800_r0/r0_binding.json"
QUARANTINE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v46_replay_quarantine_v9_outer10_v12_20260815T2330p0800/replay_quarantine.json"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def append(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def authority_probe() -> tuple[bool, str | None]:
    # Reuse the exact v43 live authority implementation.  It acquires/releases
    # only the MySQL coordination lease and performs no external mutation.
    from scripts import wait_and_launch_mysql_algorithm_c_v42_rank4_v1 as c_waiter
    from scripts import mysql_co_resident_pg_isolation_v2 as isolation

    isolation.install()
    c_waiter.configure_authority()
    return c_waiter.authority_probe()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-result", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--maximum-wait-seconds", type=float, default=72 * 3600)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--replay-quarantine-prior", type=Path)
    parser.add_argument("--derived-quarantine-output", type=Path)
    parser.add_argument(
        "--run-id-prefix", default="algorithm_b_from_c_formal_20260815",
    )
    args = parser.parse_args()
    dynamic_quarantine = (
        args.replay_quarantine_prior is not None
        or args.derived_quarantine_output is not None
    )
    if dynamic_quarantine and (
        args.replay_quarantine_prior is None
        or args.derived_quarantine_output is None
    ):
        parser.error("dynamic quarantine prior and output are both required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{8,140}", args.run_id_prefix):
        parser.error("safe Algorithm B run-id prefix required")
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = output_root / "algorithm_b_auto_launch.jsonl"
    result_path = output_root / "algorithm_b_auto_launch_result.json"
    if result_path.exists():
        if load(result_path).get("status") == "STARTED":
            return 0
        raise RuntimeError("existing Algorithm B auto-launch result is terminal")
    bound_inputs = [LAUNCHER, PREFLIGHT, PREPARED, ACCOUNTS, R0]
    bound_inputs.append(
        args.replay_quarantine_prior.expanduser().resolve()
        if dynamic_quarantine else QUARANTINE
    )
    for path in bound_inputs:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"bound regular input differs: {path}")
    append(log_path, {
        "event": "algorithm_b_auto_launch_wait_started",
        "captured_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "external_process_mutation_prohibited": True,
        "maximum_wait_seconds": args.maximum_wait_seconds,
    })
    started = time.monotonic()
    attempt = 0
    last_error: str | None = None
    while time.monotonic() - started < args.maximum_wait_seconds:
        handoff_path = args.handoff_result.expanduser().resolve()
        if not handoff_path.exists():
            time.sleep(args.poll_seconds)
            continue
        handoff = load(handoff_path)
        if handoff.get("status") != "PREPARED_FOR_ALGORITHM_B":
            raise RuntimeError(f"C to B handoff failed: {handoff.get('status')}")
        release_path = Path(str(dict(handoff["algorithm_b_interval_release"])["path"])).resolve()
        checkpoint_path = Path(str(dict(handoff["algorithm_c_final_checkpoint"])["path"])).resolve()
        if (
            sha(release_path) != dict(handoff["algorithm_b_interval_release"])["sha256"]
            or sha(checkpoint_path) != dict(handoff["algorithm_c_final_checkpoint"])["sha256"]
        ):
            raise RuntimeError("C to B handoff binding differs")
        quarantine = QUARANTINE
        if dynamic_quarantine:
            prior_path = args.replay_quarantine_prior.expanduser().resolve()
            quarantine = args.derived_quarantine_output.expanduser().resolve()
            if not quarantine.exists():
                document = quarantine_rebind.build_document(
                    quarantine_rebind.load(prior_path),
                    quarantine_rebind.load(checkpoint_path),
                    prior_path=prior_path,
                    checkpoint_path=checkpoint_path,
                )
                atomic(quarantine, document)
                append(log_path, {
                    "event": "algorithm_b_final_quarantine_rebound",
                    "captured_at": now(),
                    "prior": {"path": str(prior_path), "sha256": sha(prior_path)},
                    "checkpoint": {
                        "path": str(checkpoint_path),
                        "sha256": sha(checkpoint_path),
                    },
                    "output": {"path": str(quarantine), "sha256": sha(quarantine)},
                })
            final_quarantine = load(quarantine)
            final_checkpoint = load(checkpoint_path)
            empty_mappings = (
                "quarantined_param_keys_by_scene",
                "soft_replayed_param_keys_by_scene",
                "permanent_invalid_param_keys_by_scene",
            )
            if (
                final_quarantine.get("schema_version")
                != "mysql_algorithm_c_replay_quarantine_v6"
                or final_quarantine.get("status") != "PASS"
                or final_quarantine.get("formal_state_mutation") is not False
                or final_quarantine.get("resume_checkpoint_outer_attempt")
                != final_checkpoint.get("completed_outer_attempts")
                or final_quarantine.get("freeze_sha256")
                != final_checkpoint.get("freeze_sha256")
                or any(
                    any((final_quarantine.get(field) or {}).values())
                    for field in empty_mappings
                )
            ):
                raise RuntimeError("derived final replay quarantine differs")
        passed, error = authority_probe()
        if not passed:
            if error != last_error:
                append(log_path, {
                    "event": "algorithm_b_authority_still_blocked",
                    "captured_at": now(),
                    "error_sha256": hashlib.sha256(str(error).encode()).hexdigest(),
                    "external_process_mutation_performed": False,
                })
                last_error = error
            time.sleep(args.poll_seconds)
            continue
        attempt += 1
        run_id = f"{args.run_id_prefix}_attempt{attempt:03d}"
        command = [
            str(PYTHON), str(LAUNCHER), "--run-id", run_id,
            "--algorithm-b-release", str(release_path),
            "--checkpoint", str(checkpoint_path),
            "--runtime-preflight", str(PREFLIGHT),
            "--prepared-binding", str(PREPARED),
            "--benchmark-accounts", str(ACCOUNTS),
            "--fresh-r0", str(R0),
            "--replay-quarantine", str(quarantine),
        ]
        completed = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True,
            check=False, timeout=180,
        )
        append(log_path, {
            "event": "algorithm_b_launch_attempt",
            "captured_at": now(), "attempt": attempt, "run_id": run_id,
            "returncode": completed.returncode,
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        })
        if completed.returncode == 0:
            receipt = json.loads(completed.stdout)
            result = {
                "status": "STARTED", "captured_at": now(),
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "run_id": run_id, "attempt": attempt,
                "systemd_unit": receipt["systemd_unit"],
                "run_state_path": receipt["run_state_path"],
                "launch_receipt_path": str(
                    ROOT / "history/mysql_four_scene_20260809" / run_id
                    / "launch_receipt.json"
                ),
                "external_process_mutation_performed": False,
            }
            atomic(result_path, result)
            return 0
        run_root = ROOT / "history/mysql_four_scene_20260809" / run_id
        state_path = run_root / "run_state.json"
        if state_path.exists():
            state = load(state_path)
            physical_replay = any(run_root.glob("outer_*/top5_batch_*/batch_result.json"))
            if physical_replay or any((state.get("accepted_rounds") or {}).values()):
                failure = {
                    "status": "FAILED_CLOSED", "captured_at": now(),
                    "run_id": run_id, "run_state_path": str(state_path),
                    "physical_candidate_replay_started": physical_replay,
                    "external_process_mutation_performed": False,
                }
                atomic(result_path, failure)
                return 4
        time.sleep(args.poll_seconds)
    atomic(result_path, {
        "status": "TIMED_OUT", "captured_at": now(),
        "waited_seconds": time.monotonic() - started,
        "external_process_mutation_performed": False,
    })
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
