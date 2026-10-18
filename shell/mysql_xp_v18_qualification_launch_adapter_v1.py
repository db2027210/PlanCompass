#!/usr/bin/env python3
"""Bind sealed XP v18-v23 material to the shared four-flow launcher.

The XP authority renderer already creates solo/paired commands but does
not emit an executable launch descriptor.  This offline adapter verifies the
sealed adoption, command, authority, token, and shared-dispatch identities and
emits that missing descriptor.  It never starts a process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping


sys.path.insert(0, str(Path(__file__).resolve().parent))
import mysql_xp_successor_binding_v1 as xp_binding

XP_IDS = {
    "f43f33b5d60cf6de2fe63f96852e6a46ea3b132b5d52039a1e60021771dc9262",
    "e91031f5af956af87904a240fcf6ce6673d2511dfc66f1a6337f36698133050d",
    "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f",
    "f6528795e2eabc84c7d29ff8d387bc50ffd0bbb055e4fc0987da5aa8626717c2",
    "21d1a853548ebdff573d18a82c7a124b810b5656e3e3a330146f87d3e0b460ad",
    "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e",
}


class AdapterError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path, label: str) -> dict[str, Any]:
    sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file():
        raise AdapterError("%s is absent or unsafe" % label)
    if sidecar.is_symlink() or not sidecar.is_file() or sidecar.read_text(encoding="ascii") != "%s  %s\n" % (sha256(path), path.name):
        raise AdapterError("%s sidecar differs" % label)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AdapterError("%s must be an object" % label)
    return value


def write(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8") + b"\n"
    sidecar = Path(str(path) + ".sha256")
    if any(item.exists() or item.is_symlink() for item in (path, sidecar)):
        raise AdapterError("refusing to overwrite XP launch descriptor")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)
    sidecar.write_text("%s  %s\n" % (hashlib.sha256(payload).hexdigest(), path.name), encoding="ascii")
    sidecar.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adopted-service", type=Path, required=True)
    parser.add_argument("--command-spec", type=Path, required=True)
    parser.add_argument("--main-agent-authority", type=Path, required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--shared-dispatch", type=Path, required=True)
    parser.add_argument("--xp-successor-binding", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--peer-readback", type=Path)
    parser.add_argument("--peer-container")
    args = parser.parse_args()
    bundle = args.bundle.resolve(strict=True)
    binding = xp_binding.validate_binding(args.xp_successor_binding, bundle)
    if bundle.name not in XP_IDS:
        raise AdapterError("supported XP successor bundle differs")
    xp_id = bundle.name
    manifest = load(bundle / "bundle_manifest.json", "XP bundle manifest")
    adoption = load(args.adopted_service, "XP adopted service")
    command = load(args.command_spec, "XP qualification command")
    authority = load(args.main_agent_authority, "XP qualification authority")
    token_path = args.main_agent_token_file
    if token_path.is_symlink() or not token_path.is_file() or stat.S_IMODE(token_path.stat().st_mode) != 0o600:
        raise AdapterError("XP main-agent token differs")
    secret = token_path.read_text(encoding="ascii").strip()
    mode = command.get("mode")
    paired = mode == "qualification_paired"
    private = adoption.get("private_credentials") or {}
    database = adoption.get("database") or {}
    client = adoption.get("client") or {}
    metric = Path(str((command.get("workload_metric_stream") or {}).get("path") or ""))
    credential_root = Path(str(private.get("directory") or ""))
    mysql_defaults = credential_root / "mysql-client.cnf"
    if (
        manifest.get("bundle_id") != xp_id
        or adoption.get("status") != "PASS_ADOPTED_SAME_LIVE_SERVICE"
        or adoption.get("bundle_id") != xp_id
        or mode not in ("qualification_solo", "qualification_paired")
        or authority.get("mode") != mode
        or authority.get("qualification_attempt_id") != command.get("qualification_attempt_id")
        or authority.get("command_spec_sha256") != sha256(args.command_spec)
        or authority.get("main_agent_token_sha256") != hashlib.sha256(secret.encode("utf-8")).hexdigest()
        or authority.get("accepted_round_authorized") is not False
        or not metric.is_absolute()
        or metric.name != "workload_metrics.jsonl"
        or not isinstance(database.get("name"), str)
        or not isinstance(client.get("name"), str)
        or private.get("mysql_defaults") != "mysql-client.cnf"
        or not credential_root.is_absolute()
        or mysql_defaults.is_symlink()
        or not mysql_defaults.is_file()
        or len(secret) < 32
        or any(character.isspace() for character in secret)
        or paired != bool(args.peer_readback and args.peer_container)
    ):
        raise AdapterError("XP launch binding differs")
    result = {
        "schema_version": "mysql_shared_qualification_launch_descriptor_v1",
        "status": "MATERIALIZED_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "mode": mode,
        "scenario_backend": "xp",
        "attempt_token": adoption.get("attempt_id"),
        "qualification_attempt_id": command["qualification_attempt_id"],
        "bundle": str(bundle),
        "container": database["name"],
        "mysql_client_container": client["name"],
        "mysql_defaults_file": str(mysql_defaults),
        "output": str(metric.parent / "qualification_evidence.json"),
        "command_spec": str(args.command_spec.resolve()),
        "main_agent_authority": str(args.main_agent_authority.resolve()),
        "main_agent_token_file": str(token_path.resolve()),
        "peer_readback": str(args.peer_readback.resolve()) if paired else None,
        "peer_container": args.peer_container if paired else None,
        "shared_dispatch_sha256": sha256(args.shared_dispatch.resolve(strict=True)),
        "xp_successor_binding": str(args.xp_successor_binding.resolve(strict=True)),
        "xp_successor_binding_sha256": sha256(args.xp_successor_binding.resolve(strict=True)),
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    write(args.output, result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdapterError, OSError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
