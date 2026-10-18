#!/usr/bin/env python3
"""Reset only the two timed-out XP w1 planes after formal rank-5 timeout."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v5 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm


XP_W1 = ("xp_cpu_w1", "xp_io_w1")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--failed-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    failed_root = args.failed_root.resolve()
    state_path = failed_root / "run_state.json"
    batch_path = failed_root / "outer_006/top5_batch_5/batch_result.json"
    prior_path = failed_root / "xp_resets/outer_006_batch_5/reset_binding.json"
    state, batch, prior = load(state_path), load(batch_path), load(prior_path)
    freeze_path = Path(str(state.get("freeze_path") or "")).resolve()
    freeze = load(freeze_path)
    timeout_tasks = {
        str(row.get("task")) for row in (batch.get("failures") or [])
        if str(row.get("error") or "").startswith("TimeoutExpired:")
    }
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or int(state.get("outer_attempt") or 0) != 6
        or "information_schema.TABLES" not in str(state.get("error") or "")
        or batch.get("status") != "FAILED_CLOSED"
        or not {"xp_cpu_w1", "xp_io_w1"}.issubset(timeout_tasks)
        or not (900 <= float(batch.get("elapsed_seconds") or 0) <= 910)
        or prior.get("status") != "PASS"
        or prior.get("freeze_sha256") != sha(freeze_path)
        or any(
            not (failed_root / f"outer_006/{scene}.decision.json").is_file()
            for scene in algorithm.SCENES
        )
    ):
        raise RuntimeError("post-timeout XP recovery authority differs")
    controller_pid = int(state.get("pid") or 0)
    if controller_pid and Path(f"/proc/{controller_pid}").exists():
        raise RuntimeError("failed formal controller remains alive")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        binding = output / "recovered_xp_binding.json"
        recovered = load(binding)
        reset_rows = {
            str(row["worker_id"]): dict(row)
            for row in (recovered.get("reset_workers") or [])
        }
        quarantine = (
            Path(str(freeze["storage"]["runtime_root"]))
            / "quarantine" / output.name / "post_timeout_w1_reset_v1"
        )
        if (
            recovered.get("status") != "PASS"
            or recovered.get("failed_state_sha256") != sha(state_path)
            or recovered.get("failed_batch_sha256") != sha(batch_path)
            or set(reset_rows) != set(XP_W1)
        ):
            raise RuntimeError("existing post-timeout recovery differs")
        cleanup = reset.purge_completed_discard_quarantine(
            quarantine_root=quarantine,
            reset_workers=reset_rows,
            binding_path=binding,
            cleanup_path=output / "quarantine_cleanup.json",
        )
        print(json.dumps({
            "status": "PASS", "binding": str(binding),
            "resumed_cleanup_only": True,
            "quarantine_deleted_bytes": cleanup["deleted_bytes"],
        }, sort_keys=True))
        return 0
    stage = output / "xp_resets/post_timeout_w1_reset_v1"
    stage.mkdir(parents=True, mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / output.name / stage.name
    )
    frozen = {str(row["worker_id"]): row for row in freeze["workers"]}
    previous = {str(row["worker_id"]): row for row in prior["workers"]}
    started = time.monotonic()
    reset_rows: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-timeout-reset") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], previous[worker_id], quarantine, stage,
                True, "formal_point", True, False, True, False,
            ): worker_id
            for worker_id in XP_W1
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            reset_rows[worker_id] = future.result()

    account_root = stage / "accounts"
    account_root.mkdir()
    for worker_id in XP_W1:
        account = users._run_worker(
            reset_rows[worker_id], account_root, verify_existing=True,
            rotate_all=False,
        )
        reset_rows[worker_id].update(account)
    if (
        set(reset_rows) != set(XP_W1)
        or any(row.get("ready") is not True for row in reset_rows.values())
        or any(algorithm.xp_active_client_session_count(row) != 0
               for row in reset_rows.values())
    ):
        raise RuntimeError("post-timeout XP recovery readback differs")
    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_post_timeout_xp_w1_recovery_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "failed_state_path": str(state_path),
        "failed_state_sha256": sha(state_path),
        "failed_batch_path": str(batch_path),
        "failed_batch_sha256": sha(batch_path),
        "predecessor_xp_binding_path": str(prior_path),
        "predecessor_xp_binding_sha256": sha(prior_path),
        "freeze_path": str(freeze_path),
        "freeze_sha256": sha(freeze_path),
        "reset_workers": [reset_rows[worker_id] for worker_id in XP_W1],
        "companion_workers_untouched": ["xp_cpu_w2", "xp_io_w2"],
        "accepted_state_mutation": False,
        "elapsed_seconds": time.monotonic() - started,
    })
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers=reset_rows,
        binding_path=binding,
        cleanup_path=output / "quarantine_cleanup.json",
    )
    print(json.dumps({
        "status": "PASS", "binding": str(binding),
        "elapsed_seconds": time.monotonic() - started,
        "quarantine_deleted_bytes": cleanup["deleted_bytes"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
