#!/usr/bin/env python3
"""Publish Algorithm C runtime v44 for nested technical-timeout continuation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v43"
    / "optimization_contract.json"
)
NEW = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v44"
    / "optimization_contract.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v2.py"
WAITER = ROOT / "scripts/wait_and_launch_mysql_algorithm_c_v42_rank4_v1.py"
TEST = ROOT / "tests/test_mysql_algorithm_c_boundary_technical_isolation.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def bind(bindings: dict, label: str, path: Path) -> None:
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        if not key.endswith("_path"):
            continue
        digest_key = key[:-5] + "_sha256"
        if digest_key not in bindings:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"runtime release binding differs: {path}")
        bindings[digest_key] = sha(path)


def main() -> int:
    if NEW.exists():
        raise RuntimeError(f"fresh v44 release directory required: {NEW.parent}")
    value = json.loads(OLD.read_text(encoding="utf-8"))
    value["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v44"
    value["predecessor"] = {"path": rel(OLD), "sha256": sha(OLD)}
    value["successor_reason"] = (
        "preserve an already classified zero-credit technical timeout across "
        "a later runtime-interference continuation without converting it into "
        "a rejection, replay-history row, pruning signal, or convergence event"
    )
    value["nested_continuation_technical_timeout_contract"] = {
        "source_binding_must_declare_technical_timeout_excluded": True,
        "batch_must_revalidate_with_technical_timeout_classifier": True,
        "accepted_state_credit_increment": 0,
        "rejected_outer_attempt_increment": 0,
        "acceptance_tier_mutation": False,
        "convergence_mutation": False,
        "replay_history_mutation": False,
        "pruning_evidence_eligible": False,
        "physical_candidate_replay_reuse": False,
        "resume_same_unfinished_rank": True,
    }
    bindings = value["bindings"]
    rebind(bindings)
    bind(bindings, "boundary_resume_controller", CONTROLLER)
    bind(bindings, "boundary_launch_source", LAUNCHER)
    bind(bindings, "rank4_auto_wait_source", WAITER)
    bind(bindings, "boundary_technical_isolation_test", TEST)
    bind(bindings, "nested_continuation_release_builder", Path(__file__).resolve())
    NEW.parent.mkdir(parents=True, exist_ok=False)
    NEW.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    NEW.with_name(NEW.name + ".sha256").write_text(
        f"{sha(NEW)}  {NEW.name}\n", encoding="ascii",
    )
    print(json.dumps({
        "optimization_contract": str(NEW),
        "optimization_sha256": sha(NEW),
        "controller_sha256": sha(CONTROLLER),
        "launcher_sha256": sha(LAUNCHER),
        "waiter_sha256": sha(WAITER),
        "test_sha256": sha(TEST),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

