#!/usr/bin/env python3
"""Delete discard-only datadirs behind one completed reset binding."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v5 as reset


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink binding required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError("reset binding JSON object required")
    return dict(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-binding", type=Path, required=True)
    parser.add_argument("--cleanup-output", type=Path, required=True)
    args = parser.parse_args()
    binding_path = args.reset_binding.expanduser().resolve()
    cleanup_path = args.cleanup_output.expanduser().resolve()
    binding = load(binding_path)
    workers = {
        str(row["worker_id"]): dict(row)
        for row in (binding.get("workers") or [])
        if row.get("quarantined_invalid_runtime_root")
    }
    quarantine_roots = {
        Path(str(row["quarantined_invalid_runtime_root"])).parent
        for row in workers.values()
    }
    if (
        binding.get("status") not in {"PASS", "PREPARED"}
        or not workers
        or len(quarantine_roots) != 1
        or cleanup_path.parent != binding_path.parent
    ):
        raise RuntimeError("completed quarantine cleanup CLI authority differs")
    result = reset.purge_completed_discard_quarantine(
        quarantine_root=next(iter(quarantine_roots)),
        reset_workers=workers,
        binding_path=binding_path,
        cleanup_path=cleanup_path,
    )
    print(json.dumps({
        "status": result["status"],
        "deleted_worker_count": result["deleted_worker_count"],
        "deleted_bytes": result["deleted_bytes"],
        "cleanup_output": str(cleanup_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
