#!/usr/bin/env python3
"""Resume C with a bounded disk2 post-reset writeback-settle gate."""

from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v29 as v29


runtime = v29.runtime
ORIGINAL_RESET_XP = runtime.alg.reset_xp
ORIGINAL_V29_STATE_DOCUMENT = v29.state_document
DISK2_DEVICE_STAT = Path("/sys/class/block/nvme2n1/stat")
QUIET_REQUIRED_SECONDS = 15
QUIET_MAXIMUM_SECONDS = 120
MAX_WRITE_BYTES_PER_SECOND = 8 * 1024 * 1024
MAX_IN_FLIGHT_IOS = 1


def _disk2_stat() -> tuple[int, int]:
    fields = DISK2_DEVICE_STAT.read_text(encoding="ascii").split()
    if len(fields) < 11:
        raise RuntimeError("disk2 block-device stat is incomplete")
    return int(fields[6]), int(fields[8])


def reset_xp(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    output: Path, token: str, *, worker_ids: set[str] | None = None,
) -> None:
    ORIGINAL_RESET_XP(
        freeze, workers, output, token, worker_ids=worker_ids,
    )
    runtime_root = Path(str(freeze["storage"]["runtime_root"])).resolve()
    if (
        runtime_root != Path("/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4")
        or not runtime_root.is_dir()
        or not DISK2_DEVICE_STAT.is_file()
    ):
        raise RuntimeError("post-reset disk2 settle authority differs")
    stage = output / "xp_resets" / token
    evidence_path = stage / "post_reset_storage_settle.json"
    if evidence_path.exists() or evidence_path.is_symlink():
        raise RuntimeError("fresh post-reset settle evidence required")

    sync_started = time.monotonic()
    synced = subprocess.run(
        ["sync", "-f", str(runtime_root)],
        text=True, capture_output=True, check=False, timeout=300,
    )
    sync_seconds = time.monotonic() - sync_started
    if synced.returncode != 0:
        raise RuntimeError(
            "disk2 syncfs failed after XP reset: " + synced.stderr[-500:]
        )

    quiet_started = time.monotonic()
    previous_sectors, _ = _disk2_stat()
    consecutive = 0
    samples: list[dict[str, Any]] = []
    while time.monotonic() - quiet_started < QUIET_MAXIMUM_SECONDS:
        sample_started = time.monotonic()
        time.sleep(1.0)
        sectors, in_flight = _disk2_stat()
        elapsed = max(1e-9, time.monotonic() - sample_started)
        write_bps = max(0, sectors - previous_sectors) * 512.0 / elapsed
        previous_sectors = sectors
        passed = (
            write_bps <= MAX_WRITE_BYTES_PER_SECOND
            and in_flight <= MAX_IN_FLIGHT_IOS
        )
        consecutive = consecutive + 1 if passed else 0
        samples.append({
            "elapsed_seconds": time.monotonic() - quiet_started,
            "write_bytes_per_second": write_bps,
            "in_flight_ios": in_flight,
            "sample_passed": passed,
            "consecutive_quiet_seconds": consecutive,
        })
        if consecutive >= QUIET_REQUIRED_SECONDS:
            break

    passed = consecutive >= QUIET_REQUIRED_SECONDS
    document = {
        "schema_version": "mysql_algorithm_c_post_reset_storage_settle_v1",
        "status": "PASS" if passed else "FAILED_CLOSED",
        "created_at": runtime.alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "runtime_root": str(runtime_root),
        "block_device": "nvme2n1",
        "syncfs_command": ["sync", "-f", str(runtime_root)],
        "syncfs_returncode": synced.returncode,
        "syncfs_seconds": sync_seconds,
        "quiet_required_seconds": QUIET_REQUIRED_SECONDS,
        "quiet_maximum_seconds": QUIET_MAXIMUM_SECONDS,
        "maximum_write_bytes_per_second": MAX_WRITE_BYTES_PER_SECOND,
        "maximum_in_flight_ios": MAX_IN_FLIGHT_IOS,
        "quiet_elapsed_seconds": time.monotonic() - quiet_started,
        "samples": samples,
        "workload_started_before_gate": False,
        "accepted_state_credit_mutated": False,
        "workload_or_resource_envelope_changed": False,
    }
    runtime.alg.atomic(evidence_path, document)
    if not passed:
        raise RuntimeError("disk2 post-reset writeback did not settle in 120s")


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_V29_STATE_DOCUMENT(**kwargs)
    document["post_reset_storage_settle_release"] = {
        "release_version": "v32",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": runtime.alg.sha(Path(__file__).resolve()),
        "disk2_syncfs_required": True,
        "quiet_required_seconds": QUIET_REQUIRED_SECONDS,
        "quiet_maximum_seconds": QUIET_MAXIMUM_SECONDS,
        "maximum_write_bytes_per_second": MAX_WRITE_BYTES_PER_SECOND,
        "maximum_in_flight_ios": MAX_IN_FLIGHT_IOS,
        "measurement_timeout_unchanged_seconds": 900,
        "workload_contract_unchanged": True,
        "resource_envelope_unchanged": True,
    }
    return document


def main() -> int:
    runtime.alg.reset_xp = reset_xp
    v29.state_document = state_document
    return v29.main()


if __name__ == "__main__":
    raise SystemExit(main())
