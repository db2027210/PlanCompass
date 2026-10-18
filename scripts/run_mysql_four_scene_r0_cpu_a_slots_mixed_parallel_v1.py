#!/usr/bin/env python3
"""R0 authority adapter for the frozen 2/2/4/4 mixed scene layout."""

from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "scripts/run_mysql_four_scene_r0_v5.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
_replacements = {
    "or len(prepared.get(\"workers\") or []) != 8":
        "or len(prepared.get(\"workers\") or []) != 12",
    "or len(accounts.get(\"workers\") or []) != 8":
        "or len(accounts.get(\"workers\") or []) != 12",
    "AUTHORITY_SCENE_SCOPE is not None and len(authority_worker_ids) != 2":
        "AUTHORITY_SCENE_SCOPE is not None and len(authority_worker_ids) != "
        "(4 if AUTHORITY_SCENE_SCOPE in {\"hybench_xp_cpu\", \"hybench_xp_io\"} else 2)",
}
for _old, _new in _replacements.items():
    if _source.count(_old) != 1:
        raise RuntimeError(f"mixed-parallel R0 predecessor differs: {_old}")
    _source = _source.replace(_old, _new)
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_four_scene_r0_cpu_a_slots_mixed_parallel_v1_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)
for _name, _value in _impl.items():
    if not _name.startswith("__"):
        globals()[_name] = _value


def configure_runtime_authority(
    *, freeze: Path, freeze_schema: str, runtime_preflight: Path,
    prepared: Path, benchmark_accounts: Path, preflight_source: Path,
    scene: str,
) -> None:
    """Update both adapter exports and the exec-loaded function globals."""
    values = {
        "FREEZE": freeze.resolve(),
        "FREEZE_SCHEMA": freeze_schema,
        "PREFLIGHT": runtime_preflight.resolve(),
        "RUNTIME_PREFLIGHT": runtime_preflight.resolve(),
        "PREPARED": prepared.resolve(),
        "BENCHMARK_ACCOUNTS": benchmark_accounts.resolve(),
        "PREFLIGHT_SOURCE": preflight_source.resolve(),
        "AUTHORITY_SCENE_SCOPE": scene,
    }
    _impl.update(values)
    globals().update(values)
