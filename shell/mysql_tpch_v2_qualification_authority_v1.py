#!/usr/bin/env python3
"""Seal one short-lived TPC-H debug-qualification authority offline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Mapping


HEX64 = re.compile(r"[0-9a-f]{64}$")
MODES = {"qualification_solo", "qualification_paired"}


class RenderError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sealed(path: Path, label: str) -> dict[str, Any]:
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file():
        raise RenderError(f"{label} must be a regular file")
    expected = f"{sha256(path)}  {path.name}\n"
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise RenderError(f"{label} sidecar differs")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RenderError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RenderError(f"{label} must be a JSON object")
    return value


def verify_bundle(bundle: Path) -> dict[str, Any]:
    bundle = Path(bundle).resolve(strict=True)
    manifest = load_sealed(bundle / "bundle_manifest.json", "bundle manifest")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != bundle.name
        or HEX64.fullmatch(str(bundle_id or "")) is None
        or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id
        or manifest.get("schema_version")
        != "mysql_tpch_distributed_formal10_successor_v2"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise RenderError("TPC-H bundle identity/scope differs")
    return manifest


def read_token(path: Path) -> str:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RenderError("token must be a regular file")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise RenderError("token file mode must be 0600")
    token = path.read_text(encoding="ascii").strip()
    if (
        len(token) < 32
        or len(token) > 512
        or any(character.isspace() for character in token)
    ):
        raise RenderError("token content differs")
    return token


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if any(candidate.exists() or candidate.is_symlink() for candidate in (path, sidecar)):
        raise RenderError("refusing to overwrite qualification authority")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    sidecar_temporary = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        with sidecar_temporary.open("xb") as stream:
            stream.write(
                f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n".encode(
                    "ascii"
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.replace(sidecar_temporary, sidecar)
        os.chmod(path, 0o600)
        os.chmod(sidecar, 0o600)
    finally:
        for temporary_path in (temporary, sidecar_temporary):
            if temporary_path.exists():
                temporary_path.unlink()


def render(
    bundle: Path,
    command_path: Path,
    token_path: Path,
    output: Path,
    valid_seconds: int = 900,
    now: float | None = None,
) -> dict[str, Any]:
    manifest = verify_bundle(bundle)
    command = load_sealed(command_path, "qualification command")
    token = read_token(token_path)
    mode = command.get("mode")
    attempt = command.get("qualification_attempt_id")
    if (
        command.get("schema_version")
        != "mysql_algorithm_c_debug_qualification_command_v1"
        or command.get("bundle_id") != manifest["bundle_id"]
        or command.get("bundle_manifest_sha256")
        != sha256(Path(bundle) / "bundle_manifest.json")
        or mode not in MODES
        or not isinstance(attempt, str)
        or len(attempt) < 32
        or command.get("debug_only") is not True
        or command.get("capture_authorized") is not False
        or command.get("formal_r0_authorized") is not False
        or command.get("formal_authorized") is not False
        or command.get("accepted_round_authorized") is not False
        or command.get("cwd") != "/measurement"
    ):
        raise RenderError("qualification command authority boundary differs")
    valid_seconds = int(valid_seconds)
    if valid_seconds < 240 or valid_seconds > 3600:
        raise RenderError("authority validity must be 240..3600 seconds")
    observed = float(time.time() if now is None else now)
    authority = {
        "schema_version":
            "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "authorized_by": "main_agent",
        "bundle_id": manifest["bundle_id"],
        "mode": mode,
        "qualification_attempt_id": attempt,
        "main_agent_token_sha256": hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest(),
        "command_spec_sha256": sha256(command_path),
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "not_before_epoch": observed - 1.0,
        "expires_epoch": observed + float(valid_seconds),
    }
    atomic_json(output, authority)
    return authority


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--command-spec", type=Path, required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--valid-seconds", type=int, default=900)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = render(
        args.bundle,
        args.command_spec,
        args.main_agent_token_file,
        args.output,
        args.valid_seconds,
    )
    print(
        json.dumps(
            {
                "status": "SEALED_QUALIFICATION_AUTHORITY_NOT_LAUNCHED",
                "authority": str(args.output),
                "authority_sha256": sha256(args.output),
                "bundle_id": result["bundle_id"],
                "mode": result["mode"],
                "qualification_attempt_id":
                    result["qualification_attempt_id"],
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, RenderError) as exc:
        raise SystemExit(f"NOT_READY: {exc}")
