#!/usr/bin/env python3
"""TPC-H v42 compatibility runner with a normalized successful exit code."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any


V40 = Path(__file__).with_name(
    "run_tpch_round29_diagnostic_exact_plan_regime_batch_v40.py"
)
V40_SHA256 = "c3d352b980daf403a3344b7381ad5699215f6b2ba59b2a40c10b6e71f91a908d"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_v40() -> Any:
    if not V40.is_file() or sha256(V40) != V40_SHA256:
        raise RuntimeError("TPC-H v42 sealed v40 runner SHA mismatch")
    specification = importlib.util.spec_from_file_location(
        "tpch_v42_v40_compatibility", V40
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load sealed TPC-H v40 runner")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


v40 = load_v40()


def normalize_exit_code(value: Any) -> int:
    """The sealed runner reports success as either None or integer zero."""
    if value is None:
        return 0
    result = int(value)
    if result != 0:
        raise RuntimeError(f"sealed diagnostic runner returned nonzero status: {result}")
    return result


def main() -> int:
    # v40 has already installed the narrow selection-contract translation on
    # its sealed original module.  Bypass only v40.main's erroneous int(None).
    return normalize_exit_code(v40.original.main())


if __name__ == "__main__":
    raise SystemExit(main())
