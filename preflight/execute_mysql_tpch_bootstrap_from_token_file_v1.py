#!/usr/bin/env python3
"""Invoke a sealed TPC-H bootstrap authority without exposing its token in argv."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys


# Loading a sealed bootstrap authority is strictly read-only.
sys.dont_write_bytecode = True


def load_module(path: Path):
    module_spec = importlib.util.spec_from_file_location(
        "sealed_mysql_tpch_bootstrap_authority", path
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError("sealed authority cannot be loaded")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    token_stat = args.token_file.stat()
    if not stat.S_ISREG(token_stat.st_mode):
        raise RuntimeError("token file must be regular")
    if stat.S_IMODE(token_stat.st_mode) != 0o600:
        raise RuntimeError("token file mode must be 0600")
    if token_stat.st_uid != os.getuid():
        raise RuntimeError("token file owner differs")
    token = args.token_file.read_text(encoding="ascii").strip()
    if not token:
        raise RuntimeError("token file is empty")

    authority = load_module(args.authority.resolve(strict=True))
    if hasattr(authority, "verify_sidecar"):
        authority.verify_sidecar(args.spec, "bootstrap spec")
        spec = authority.load_object(args.spec, "bootstrap spec")
    elif hasattr(authority, "load_with_sidecar"):
        spec = authority.load_with_sidecar(args.spec, "bootstrap spec")
    else:
        raise RuntimeError("sealed authority lacks a sidecar-bound spec loader")
    value = authority.execute_bootstrap(spec, args.output, token)
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
