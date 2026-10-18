#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import runpy
import signal
import sys
from pathlib import Path
from typing import Any, Dict, List


def _ignore(_signum: int, _frame: object) -> None:
    return


def _load_spec(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("launch spec must be a JSON object")
    return data


def _string_list(value: object, name: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a list of strings")
    return list(value)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_from_file.py <launch-spec.json>", file=sys.stderr, flush=True)
        return 2

    for signum in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(signum, _ignore)
        except Exception:
            pass

    spec_path = Path(sys.argv[1]).resolve()
    spec = _load_spec(spec_path)
    script = spec.get("script")
    config = spec.get("config")
    output_dir = spec.get("output_dir")
    args = _string_list(spec.get("args", []), "args")
    extra_env = spec.get("env", {})

    if not isinstance(script, str) or not script:
        raise ValueError("script is required")
    if not isinstance(config, str) or not config:
        raise ValueError("config is required")
    if not isinstance(output_dir, str) or not output_dir:
        raise ValueError("output_dir is required")
    if not isinstance(extra_env, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in extra_env.items()
    ):
        raise ValueError("env must be an object of string keys and string values")

    os.environ.update(extra_env)
    script_path = str(Path(script).resolve())
    sys.argv = [
        script_path,
        "--config",
        str(Path(config).resolve()),
        "--output-dir",
        str(Path(output_dir).resolve()),
        *args,
    ]
    runpy.run_path(script_path, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
