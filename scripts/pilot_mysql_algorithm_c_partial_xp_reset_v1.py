#!/usr/bin/env python3
"""Validate that a single-candidate batch resets only its assigned XP workers."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm


RESET_IDS = {"xp_cpu_w1", "xp_io_w1"}
COMPANION_IDS = {"xp_cpu_w2", "xp_io_w2"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--last-reset-binding", type=Path, required=True)
    args = parser.parse_args()
    source = args.last_reset_binding.expanduser().resolve()
    prior = load(source)
    freeze = algorithm.load(algorithm.FREEZE)
    workers = {
        str(row["worker_id"]): dict(row)
        for row in (prior.get("workers") or [])
    }
    if (
        prior.get("status") != "PASS"
        or prior.get("freeze_sha256") != algorithm.sha(algorithm.FREEZE)
        or set(workers) != RESET_IDS | COMPANION_IDS
        or any(row.get("ready") is not True for row in workers.values())
        or any(algorithm.xp_active_client_session_count(row) != 0 for row in workers.values())
    ):
        raise RuntimeError("partial XP reset pilot authority differs")
    prior_ids = {
        worker_id: str(row["container_id"])
        for worker_id, row in workers.items()
    }
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh pilot output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    token = "partial_single_candidate_xp_reset_v1"
    algorithm.reset_xp(
        freeze, workers, output, token, worker_ids=set(RESET_IDS),
    )
    binding = output / "xp_resets" / token / "reset_binding.json"
    result = load(binding)
    current = {
        str(row["worker_id"]): dict(row)
        for row in (result.get("workers") or [])
    }
    if (
        result.get("status") != "PASS"
        or set(result.get("reset_worker_ids") or []) != RESET_IDS
        or int(result.get("reset_concurrency") or 0) != 2
        or set(current) != RESET_IDS | COMPANION_IDS
        or any(row.get("ready") is not True for row in current.values())
        or any(current[worker_id]["container_id"] != prior_ids[worker_id] for worker_id in COMPANION_IDS)
        or any(current[worker_id].get("container_creation_avoided") is not True for worker_id in RESET_IDS)
        or any(algorithm.xp_active_client_session_count(row) != 0 for row in current.values())
    ):
        raise RuntimeError("partial XP reset pilot result differs")
    document = {
        "schema_version": "mysql_algorithm_c_partial_xp_reset_pilot_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "workload_started": False,
        "formal_outer_started": False,
        "source_reset_binding_path": str(source),
        "source_reset_binding_sha256": sha(source),
        "reset_binding_path": str(binding),
        "reset_binding_sha256": sha(binding),
        "reset_worker_ids": sorted(RESET_IDS),
        "companion_worker_ids": sorted(COMPANION_IDS),
        "companion_container_ids_unchanged": True,
        "elapsed_seconds": result["elapsed_seconds"],
        "algorithm_source_path": str(Path(algorithm.__file__).resolve()),
        "algorithm_source_sha256": sha(Path(algorithm.__file__).resolve()),
    }
    pilot = output / "pilot_result.json"
    atomic(pilot, document)
    print(json.dumps({
        "status": "PASS", "elapsed_seconds": document["elapsed_seconds"],
        "pilot": str(pilot), "reset_binding": str(binding),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
