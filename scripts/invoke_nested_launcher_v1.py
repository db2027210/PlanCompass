#!/usr/bin/env python3
"""Invoke a generated launcher without assuming its implementation nesting depth."""

from __future__ import annotations

import runpy
from pathlib import Path
import sys
from typing import Any, Mapping


def _find_main(namespace: Mapping[str, Any]) -> Any:
    current: Mapping[str, Any] = namespace
    seen: set[int] = set()
    while True:
        if id(current) in seen:
            raise RuntimeError("generated launcher implementation cycle")
        seen.add(id(current))
        main = current.get("main")
        if callable(main):
            return main
        nested = current.get("implementation")
        if not isinstance(nested, Mapping):
            raise RuntimeError("generated launcher main is absent")
        current = nested


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: invoke_nested_launcher_v1.py LAUNCHER [ARGS ...]")
    launcher = Path(sys.argv[1]).expanduser().resolve()
    if not launcher.is_file() or launcher.suffix != ".py":
        raise RuntimeError(f"regular Python launcher required: {launcher}")
    forwarded = [str(launcher), *sys.argv[2:]]
    namespace = runpy.run_path(str(launcher), run_name="scripts._nested_launcher_entry")
    sys.argv = forwarded
    return int(_find_main(namespace)())


if __name__ == "__main__":
    raise SystemExit(main())
