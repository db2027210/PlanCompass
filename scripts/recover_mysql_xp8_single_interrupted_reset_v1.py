#!/usr/bin/env python3
"""Complete one XP8 reset whose predecessor was already discarded.

This is a technical data-plane recovery only.  It creates no candidate,
accepted-round, rejected-round, or convergence credit.  A fresh live authority
must be generated from the recovered current identities before a formal
controller is launched.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v5 as reset8


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=("hybench_xp_cpu", "hybench_xp_io"), required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--source-reset-binding", type=Path, required=True)
    parser.add_argument("--failed-output", type=Path, required=True)
    parser.add_argument("--reset-token", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    freeze_path = args.freeze.resolve()
    source_path = args.source_reset_binding.resolve()
    failed_output = args.failed_output.resolve()
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh recovery output required: {output}")
    freeze = load(freeze_path)
    source = load(source_path)
    prefix = "xp_cpu" if args.scene == "hybench_xp_cpu" else "xp_io"
    expected = {f"{prefix}_w{i}" for i in range(1, 9)}
    if args.worker_id not in expected or source.get("status") != "PASS":
        raise RuntimeError("single-worker recovery authority differs")
    workers = {
        str(row["worker_id"]): dict(row)
        for row in source.get("workers") or []
        if row.get("worker_id") in expected
    }
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in freeze.get("workers") or []
        if row.get("worker_id") in expected
    }
    if set(workers) != expected or set(frozen) != expected:
        raise RuntimeError("XP8 recovery worker set differs")

    reset8.install_for_scene(workers)
    reset8.configure(freeze=freeze_path, prepared=source_path)
    stage = failed_output / "xp_resets" / args.reset_token
    old_target = (
        Path(str((freeze.get("storage_by_worker") or {}).get(args.worker_id, freeze["storage"])["runtime_root"]))
        / "quarantine" / failed_output.name / args.reset_token / args.worker_id
    )
    runtime_root = Path(str(frozen[args.worker_id].get("runtime_root") or ""))
    if not old_target.is_dir() or not runtime_root.is_dir():
        raise RuntimeError(
            f"interrupted reset boundary differs: old={old_target} runtime={runtime_root}"
        )
    container = str(workers[args.worker_id]["container"])
    if reset8.predecessor.base._impl["_container_id"](container):
        raise RuntimeError(f"stopped broken container must be removed first: {container}")

    recovered = reset8._prepare_fresh_xp(
        freeze,
        frozen[args.worker_id],
        workers[args.worker_id],
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / failed_output.name / args.reset_token,
        stage,
        True,
        "formal_point",
        allow_unresponsive_old_container=True,
        allow_bootstrap_touched_staging=True,
        allow_absent_predecessor_after_interrupted_discard=True,
    )
    account_root = stage / "accounts"
    account_root.mkdir(exist_ok=True)
    account = users._run_worker(recovered, account_root, True, False)
    row = {**frozen[args.worker_id], **recovered, **account}
    atomic(output / "recovery_binding.json", {
        "schema_version": "mysql_xp8_single_interrupted_reset_recovery_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": args.scene,
        "worker_id": args.worker_id,
        "worker": row,
        "source_reset_binding_path": str(source_path),
        "source_reset_binding_sha256": sha(source_path),
        "failed_output": str(failed_output),
        "reset_token": args.reset_token,
        "candidate_measurements_reused": 0,
        "accepted_or_rejected_round_credit": 0,
        "fresh_formal_baseline_still_required": True,
    })
    print(json.dumps({
        "status": "PASS", "worker_id": args.worker_id,
        "container": row["container"], "container_id": row["container_id"],
        "binding": str(output / "recovery_binding.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
