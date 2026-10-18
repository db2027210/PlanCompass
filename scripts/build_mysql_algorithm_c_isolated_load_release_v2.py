#!/usr/bin/env python3
"""Publish v32 with launcher/controller activation bound to v38 isolation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v31/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v32/optimization_contract.json"
FAILED_STATE = ROOT / "history/mysql_four_scene_20260809/v31_formal_outer9_resume_20260815T0845p0800/run_state.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def bind(bindings: dict, label: str, path: Path) -> None:
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        sha_key = key[:-5] + "_sha256" if key.endswith("_path") else None
        if sha_key is None or sha_key not in bindings:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"runtime release binding differs: {path}")
        bindings[sha_key] = sha(path)


def main() -> int:
    failed = json.loads(FAILED_STATE.read_text(encoding="utf-8"))
    if (
        failed.get("status") != "RUNNING"
        or failed.get("stage") != "FORMAL_INCUMBENT_BASELINE_RESETTING_XP"
        or not str(failed.get("optimization_contract_path") or "").endswith(
            "frozen_mysql_algorithm_c_outer_runtime_20260815_v30/optimization_contract.json"
        )
        or failed.get("completed_predecessor_outer_attempts") != 8
    ):
        raise RuntimeError("v31 activation mismatch evidence differs")
    value = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    value["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v32"
    value["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    value["successor_reason"] = (
        "bind both launcher and boundary controller to the same isolated-load "
        "runtime release; measurement and accepted state remain unchanged"
    )
    bindings = value["bindings"]
    bind(bindings, "isolated_load_activation_builder", Path(__file__).resolve())
    bind(bindings, "isolated_load_activation_mismatch_state", FAILED_STATE)
    rebind(bindings)
    NEW_OPT.parent.mkdir(parents=True, exist_ok=False)
    NEW_OPT.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    NEW_OPT.with_name(NEW_OPT.name + ".sha256").write_text(
        f"{sha(NEW_OPT)}  {NEW_OPT.name}\n", encoding="ascii",
    )
    print(json.dumps({"optimization_sha256": sha(NEW_OPT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
