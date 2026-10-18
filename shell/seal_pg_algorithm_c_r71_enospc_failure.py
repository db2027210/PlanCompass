#!/usr/bin/env python3
"""Seal the r71 baseline ENOSPC failure without promoting partial evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r71"
)
EVIDENCE = ROOT / "failure_evidence/enospc_baseline_rep2"
OUTPUT = ROOT / "campaign_technical_failure_manifest.json"
SPACE_RELIEF: Dict[str, Any] = {
    "source": "/tmp/operator_ppt_pydeps",
    "destination": (
        "/disk1/resource_adaptive/root_space_relief_20260822/"
        "operator_ppt_pydeps"
    ),
    "experiment_authority_removed": False,
}
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
SETTINGS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
WORKERS = (
    (
        "job_memory_w1",
        59523,
        "fullgrid_job",
        ("1", "4.96036", "0.00874428", "0.00325", "0.0025", "1000", "0.1"),
    ),
    (
        "job_memory_w2",
        59524,
        "fullgrid_job",
        ("1", "4.96036", "0.00874428", "0.00325", "0.0025", "1000", "0.1"),
    ),
    ("tpch_memory_w1", 59521, "fullgrid_tpch_sf5", ("1", "4", "0.01", "0.005", "0.0025", "1000", "0.1")),
    ("tpch_memory_w2", 59522, "fullgrid_tpch_sf5", ("1", "4", "0.01", "0.005", "0.0025", "1000", "0.1")),
    ("xp_cpu_w1", 59513, "hybench_sf10x", ("1", "4", "0.01", "0.005", "0.0025", "1000", "0.1")),
    ("xp_cpu_w2", 59514, "hybench_sf10x", ("1", "4", "0.01", "0.005", "0.0025", "1000", "0.1")),
    ("xp_io_w1", 59515, "hybench_sf10x", ("1", "4", "0.01", "0.005", "0.0025", "1000", "0.1")),
    ("xp_io_w2", 59516, "hybench_sf10x", ("1", "4", "0.01", "0.005", "0.0025", "1000", "0.1")),
)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def files_under(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def baseline_readback() -> Dict[str, Any]:
    expression = ",".join(
        f"current_setting('{setting}')" for setting in SETTINGS
    )
    rows = []
    for name, port, database, expected in WORKERS:
        env = dict(os.environ)
        env["PGPASSWORD"] = "postgres"
        result = subprocess.run(
            [
                "psql",
                "-X",
                "-qAt",
                "-h",
                "127.0.0.1",
                "-p",
                str(port),
                "-U",
                "postgres",
                "-d",
                database,
                "-c",
                f"SELECT {expression}",
            ],
            text=True,
            capture_output=True,
            env=env,
            timeout=20.0,
            check=False,
        )
        actual = tuple(result.stdout.strip().split("|")) if result.returncode == 0 else ()
        rows.append(
            {
                "worker": name,
                "port": port,
                "database": database,
                "settings": list(SETTINGS),
                "expected": list(expected),
                "actual": list(actual),
                "passed": actual == expected,
                "returncode": result.returncode,
                "stderr": result.stderr.strip(),
            }
        )
    return {
        "contract": "algorithm_c_post_failure_baseline_readback_v1",
        "workers": rows,
        "passed": all(row["passed"] for row in rows),
    }


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite failure manifest: {OUTPUT}")
    if not EVIDENCE.is_dir():
        raise RuntimeError(f"failure evidence missing: {EVIDENCE}")
    scene_states = {}
    raw_errors = {}
    for scene in SCENES:
        state = read_json(ROOT / scene / "algorithm_c_state.json")
        scene_states[scene] = {
            "phase": state.get("phase"),
            "accepted": int(state.get("accepted") or 0),
            "round_index": int(state.get("round_index") or 0),
            "physical_workload_invocations": int(
                state.get("physical_workload_invocations") or 0
            ),
            "valid_replays": int(state.get("valid_replays") or 0),
            "technical_failures": int(state.get("technical_failures") or 0),
        }
        raw_log = (ROOT / scene / "controller.log").read_text(
            encoding="utf-8", errors="replace"
        )
        raw_errors[scene] = bool(
            "Algorithm C tmp space preflight failed: available=" in raw_log
            and "required=4294967296" in raw_log
        )
    barrier_root = next(
        path for path in (EVIDENCE / "cohort_stage").iterdir() if path.is_dir()
    )
    barrier_files = []
    for path in files_under(barrier_root):
        valid_json = False
        if path.stat().st_size > 0:
            try:
                valid_json = isinstance(
                    json.loads(path.read_text(encoding="utf-8")), dict
                )
            except (json.JSONDecodeError, OSError):
                valid_json = False
        barrier_files.append(
            {
                "path": str(path.relative_to(EVIDENCE)),
                "size": path.stat().st_size,
                "sha256": sha256(path),
                "valid_json": valid_json,
                "suffix": path.suffix,
            }
        )
    stat = os.statvfs("/tmp")
    baseline = baseline_readback()
    evidence_files = {
        str(path.relative_to(ROOT)): {
            "size": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in files_under(EVIDENCE)
    }
    state_clean = all(
        row == {
            "phase": "fresh_parallel_baseline",
            "accepted": 0,
            "round_index": 0,
            "physical_workload_invocations": 2,
            "valid_replays": 2,
            "technical_failures": 0,
        }
        for row in scene_states.values()
    )
    ready_receipts = [row for row in barrier_files if row["suffix"] == ".ready"]
    done_receipts = [row for row in barrier_files if row["suffix"] == ".done"]
    payload = {
        "contract": "algorithm_c_campaign_technical_failure_v1",
        "campaign_root": str(ROOT),
        "failure": "ENOSPC_before_baseline_repetition_2_completion_marker",
        "raw_error_present_by_scene": raw_errors,
        "barrier_stage": {
            "baseline_repetition": 2,
            "files": barrier_files,
            "ready_receipt_count": len(ready_receipts),
            "done_receipt_count": len(done_receipts),
            "cohort_size": len(SCENES),
            "partial_done_receipts_ignored": 0 < len(done_receipts) < len(SCENES),
            "all_receipts_valid_json": all(row["valid_json"] for row in barrier_files),
            "passed": False,
        },
        "scene_state_at_failure": scene_states,
        "scene_state_clean": state_clean,
        "baseline_repetition_2_promoted_to_valid": False,
        "formal_round_1_started": False,
        "any_candidate_promoted_to_accepted": False,
        "campaign_reusable_for_formal_acceptance": False,
        "restart_from_fresh_campaign_required": True,
        "technical_failure_isolated_outside_valid_and_accepted": True,
        "baseline_readback_after_stop": baseline,
        "tmp_filesystem_at_seal": {
            "available_bytes": int(stat.f_bavail) * int(stat.f_frsize),
            "free_bytes": int(stat.f_bfree) * int(stat.f_frsize),
            "minimum_required_bytes": 4 * 1024 * 1024 * 1024,
        },
        "recoverable_space_relief": SPACE_RELIEF,
        "failure_evidence_files": evidence_files,
        "sealed_at_epoch_s": time.time(),
    }
    payload["passed"] = bool(
        all(raw_errors.values())
        and state_clean
        and len(ready_receipts) == len(SCENES)
        and len(done_receipts) < len(SCENES)
        and payload["barrier_stage"]["all_receipts_valid_json"]
        and baseline["passed"]
        and not payload["baseline_repetition_2_promoted_to_valid"]
        and not payload["formal_round_1_started"]
        and not payload["any_candidate_promoted_to_accepted"]
    )
    atomic_json(OUTPUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
