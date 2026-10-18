#!/usr/bin/env python3
"""Render a sealed TPC-H bootstrap plan without starting any data-plane work."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

from mysql_tpch_distributed_v2_authority import (
    AuthorityError,
    canonical,
    command_plan,
    load_object,
    verify_sidecar,
)


class RenderError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_new(path: Path, payload: bytes) -> None:
    sidecar = Path(str(path) + ".sha256")
    if any(candidate.exists() or candidate.is_symlink() for candidate in (path, sidecar)):
        raise RenderError("refusing to overwrite bootstrap plan")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    temporary_sidecar = sidecar.with_name(
        f".{sidecar.name}.{secrets.token_hex(8)}.tmp"
    )
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        with temporary_sidecar.open("xb") as stream:
            stream.write(
                f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n".encode("ascii")
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.replace(temporary_sidecar, sidecar)
        os.chmod(path, 0o600)
        os.chmod(sidecar, 0o600)
    finally:
        for candidate in (temporary, temporary_sidecar):
            if candidate.exists():
                candidate.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify_sidecar(args.spec, "bootstrap spec")
    spec = load_object(args.spec, "bootstrap spec")
    plan = command_plan(spec)
    payload = canonical(plan)
    atomic_new(args.output, payload)
    print(json.dumps({
        "status": "PASS_RENDERED_QUALIFICATION_ONLY_BOOTSTRAP_PLAN",
        "attempt_token": plan["attempt_token"],
        "bundle_id": plan["bundle_id"],
        "plan_sha256": sha256(args.output),
        "accepted_round_authorized": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuthorityError, OSError, RenderError, UnicodeError, ValueError) as exc:
        sys.stderr.write(f"NOT_READY: {exc}\n")
        raise SystemExit(2)
