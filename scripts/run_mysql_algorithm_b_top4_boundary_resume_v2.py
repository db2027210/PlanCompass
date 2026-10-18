#!/usr/bin/env python3
"""Boundary continuation with split decision roots and four-lane baseline reuse."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PREDECESSOR = ROOT / "scripts/run_mysql_algorithm_b_top4_boundary_resume_v1.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
_replacements = {
    "PARTIAL_RANKS: tuple[int, ...] = ()\nRUNTIME_PREDECESSOR: Any = None":
        "PARTIAL_RANKS: tuple[int, ...] = ()\n"
        "COMMITTED_DECISION_ROOTS: dict[int, Path] = {}\n"
        "VERIFIED_BASELINE_ROOT: Path | None = None\n"
        "EXPECTED_BASELINE_WORKERS: int | None = None\n"
        "RUNTIME_PREDECESSOR: Any = None",
    "baseline_path = source / \"formal_independent_baseline/batch_result.json\"":
        "baseline_root = (VERIFIED_BASELINE_ROOT or source).resolve()\n"
        "        baseline_path = baseline_root / \"formal_independent_baseline/batch_result.json\"",
    "or len(baseline_rows) != 2":
        "or len(baseline_rows) != int(EXPECTED_BASELINE_WORKERS or 2)",
    "path = source / f\"outer_{outer:03d}/{SCENE}.decision.json\"":
        "decision_root = COMMITTED_DECISION_ROOTS.get(outer, source).resolve()\n"
        "            path = decision_root / f\"outer_{outer:03d}/{SCENE}.decision.json\"",
    "source / f\"outer_{outer:03d}/online_resource_model_update.json\"":
        "decision_root / f\"outer_{outer:03d}/online_resource_model_update.json\"",
    "incumbents[SCENE] = copy.deepcopy(source_state[\"incumbents\"][SCENE])":
        "source_incumbents = source_state.get(\"incumbents\") or {}\n"
        "        if SCENE in source_incumbents:\n"
        "            incumbents[SCENE] = copy.deepcopy(source_incumbents[SCENE])\n"
        "        elif EXPECTED_ACCEPTED != 0 or SCENE not in incumbents:\n"
        "            raise RuntimeError(f\"{SCENE} committed incumbent is absent\")",
}
for _old, _new in _replacements.items():
    if _source.count(_old) != 1:
        raise RuntimeError(f"boundary-v2 predecessor differs: {_old!r}")
    _source = _source.replace(_old, _new)

_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_b_top4_boundary_resume_v2_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)

_CONFIG_NAMES = (
    "SOURCE", "SCENE", "EXPECTED_ERROR", "EXPECTED_ACCEPTED",
    "EXPECTED_REJECTED", "CALIBRATION_COMPLETED", "COMMITTED_OUTER",
    "PARTIAL_OUTER", "PARTIAL_RANKS", "COMMITTED_DECISION_ROOTS",
    "VERIFIED_BASELINE_ROOT", "EXPECTED_BASELINE_WORKERS",
    "RUNTIME_PREDECESSOR", "DYNAMIC_GATE", "CONTRACT_ROOT", "CONTRACT", "COORDINATION",
)
for _name, _value in _impl.items():
    if not _name.startswith("__"):
        globals()[_name] = _value


def _sync_configuration() -> None:
    for name in _CONFIG_NAMES:
        _impl[name] = globals()[name]


def install_runtime() -> Any:
    _sync_configuration()
    return _impl["install_runtime"]()


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
