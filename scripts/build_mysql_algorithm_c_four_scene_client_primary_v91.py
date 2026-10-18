#!/usr/bin/env python3
"""Freeze v91 with launcher and live-authority builder bindings."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v90"
)
OUT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v91"
)
EXTRA = {
    "builder_v91": Path(__file__).resolve(),
    "controller_v91": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v2.py",
    "controller_v90_predecessor": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v1.py",
    "four_scene_launcher": ROOT / "scripts/launch_mysql_algorithm_c_client_primary_four_scene_v1.py",
    "live_authority_builder": ROOT / "scripts/build_mysql_algorithm_c_client_primary_live_authority_v1.py",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict) -> None:
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


def main() -> int:
    if OUT.exists() or OUT.is_symlink():
        raise RuntimeError(f"fresh v91 release required: {OUT}")
    parent = json.loads(
        (SOURCE / "algorithm_c_async_contract.json").read_text(encoding="utf-8")
    )
    optimization = json.loads(
        (SOURCE / "outer_runtime_optimization.json").read_text(encoding="utf-8")
    )
    successor = json.loads(
        (SOURCE / "global_monotonic_incumbent_contract.json").read_text(encoding="utf-8")
    )
    created = datetime.now().astimezone().isoformat()
    added = {
        name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
        for name, path in EXTRA.items()
    }
    parent.update({"created_at": created, "release_version": "v91-client-primary"})
    parent["source_bindings"].update(added)
    optimization.update({
        "created_at": created, "release_version": "v91-client-primary",
    })
    OUT.mkdir(parents=True, mode=0o700)
    atomic(OUT / "algorithm_c_async_contract.json", parent)
    atomic(OUT / "outer_runtime_optimization.json", optimization)
    successor.update({
        "created_at": created,
        "release_version": "v91-client-primary",
        "parent_contract": {
            "path": str((OUT / "algorithm_c_async_contract.json").relative_to(ROOT)),
            "sha256": sha(OUT / "algorithm_c_async_contract.json"),
        },
        "outer_runtime_optimization": {
            "path": str((OUT / "outer_runtime_optimization.json").relative_to(ROOT)),
            "sha256": sha(OUT / "outer_runtime_optimization.json"),
        },
    })
    successor["source_bindings"].update(added)
    atomic(OUT / "global_monotonic_incumbent_contract.json", successor)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED", "root": str(OUT),
        "parent_sha256": sha(OUT / "algorithm_c_async_contract.json"),
        "optimization_sha256": sha(OUT / "outer_runtime_optimization.json"),
        "contract_sha256": sha(OUT / "global_monotonic_incumbent_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
