#!/usr/bin/env python3
"""Issue a fresh TPC-H solo qualification and bind one sealed SQL adapter."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import time


class IssueError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def load_sealed(path, label):
    path = Path(path); sidecar = Path(str(path) + ".sha256")
    if (path.is_symlink() or sidecar.is_symlink() or not path.is_file()
            or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii")
            != "%s  %s\n" % (sha(path), path.name)):
        raise IssueError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise IssueError(label + " must be an object")
    return value


def replace(value, old, new):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [replace(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: replace(item, old, new) for key, item in value.items()}
    return value


def write(path, raw):
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(descriptor, raw); os.fsync(descriptor)
    finally:
        os.close(descriptor)
    sidecar = Path(str(path) + ".sha256")
    sidecar.write_text("%s  %s\n" % (sha_bytes(raw), path.name), encoding="ascii")
    os.chmod(str(sidecar), 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-command", type=Path, required=True)
    parser.add_argument("--old-authority", type=Path, required=True)
    parser.add_argument("--old-attempt", required=True)
    parser.add_argument("--new-attempt", required=True)
    parser.add_argument("--fresh-token-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--collector-bundle-id", required=True)
    parser.add_argument("--collector-manifest-sha256", required=True)
    parser.add_argument("--adapter-bundle-id", required=True)
    parser.add_argument("--adapter-path", type=Path, required=True)
    parser.add_argument("--adapter-sha256", required=True)
    parser.add_argument("--lifetime-seconds", type=int, default=1800)
    args = parser.parse_args()
    hexes = (args.collector_bundle_id, args.collector_manifest_sha256,
             args.adapter_bundle_id, args.adapter_sha256)
    if (any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for value in hexes)
            or args.old_attempt == args.new_attempt
            or not 600 <= args.lifetime_seconds <= 3600):
        raise IssueError("issuer identity or TTL differs")
    command = load_sealed(args.old_command, "old command")
    authority = load_sealed(args.old_authority, "old authority")
    flags = ("capture_authorized", "formal_r0_authorized",
             "formal_authorized", "accepted_round_authorized")
    binding = command.get("native_sql_adapter_binding") or {}
    argv = command.get("argv") or []
    code_files = command.get("code_files") or {}
    old_adapter = binding.get("path")
    old_hash = binding.get("sha256")
    if (command.get("schema_version")
            != "mysql_algorithm_c_debug_qualification_command_v1"
            or command.get("mode") != "qualification_solo"
            or command.get("debug_only") is not True
            or command.get("qualification_attempt_id") != args.old_attempt
            or command.get("paired_peer_workload") is not None
            or any(command.get(key) is not False for key in flags)
            or authority.get("schema_version")
            != "mysql_algorithm_c_main_agent_qualification_authority_v1"
            or authority.get("authorized_by") != "main_agent"
            or authority.get("qualification_attempt_id") != args.old_attempt
            or authority.get("command_spec_sha256") != sha(args.old_command)
            or authority.get("debug_only") is not True
            or any(authority.get(key) is not False for key in flags)
            or not isinstance(old_adapter, str) or code_files.get(old_adapter) != old_hash
            or len(argv) < 2 or argv[1] != old_adapter):
        raise IssueError("TPC-H solo predecessor authority differs")
    adapter_path = args.adapter_path.resolve(strict=True)
    if (adapter_path.is_symlink() or not adapter_path.is_file()
            or sha(adapter_path) != args.adapter_sha256
            or args.adapter_bundle_id not in str(adapter_path)):
        raise IssueError("new adapter content identity differs")
    token_path = args.fresh_token_file
    token = token_path.read_text(encoding="ascii").strip()
    if (token_path.is_symlink() or not token_path.is_file()
            or (token_path.stat().st_mode & 0o777) != 0o600
            or len(token) < 48 or any(char.isspace() for char in token)):
        raise IssueError("fresh token differs")
    command = replace(command, args.old_attempt, args.new_attempt)
    command["argv"][1] = str(adapter_path)
    del command["code_files"][old_adapter]
    if str(adapter_path) in command["code_files"]:
        raise IssueError("new adapter code-file collision")
    command["code_files"][str(adapter_path)] = args.adapter_sha256
    command["native_sql_adapter_binding"].update({
        "schema_version": "mysql_sql_debug_metric_adapter_binding_v3",
        "bundle_id": args.adapter_bundle_id,
        "path": str(adapter_path),
        "sha256": args.adapter_sha256,
        "q15_view_policy": "deterministic_attempt_worker_unique_identifier",
        "q15_query_semantics_changed": False,
    })
    if (command.get("qualification_attempt_id") != args.new_attempt
            or args.old_attempt in json.dumps(command, sort_keys=True)
            or command["argv"][1] != str(adapter_path)
            or command["code_files"].get(str(adapter_path)) != args.adapter_sha256
            or old_adapter in command["code_files"]):
        raise IssueError("new command binding differs")
    now = time.time(); authority = replace(authority, args.old_attempt, args.new_attempt)
    authority.update({
        "qualification_attempt_id": args.new_attempt,
        "command_spec_sha256": sha_bytes(canonical(command)),
        "main_agent_token_sha256": sha_bytes(token.encode("ascii")),
        "not_before_epoch": now - 1.0,
        "expires_epoch": now + float(args.lifetime_seconds),
        "collector_bundle_id": args.collector_bundle_id,
        "collector_manifest_sha256": args.collector_manifest_sha256,
        "qualification_only": True, "debug_only": True,
        "capture_authorized": False, "formal_r0_authorized": False,
        "formal_authorized": False, "accepted_round_authorized": False,
    })
    if args.output_dir.exists():
        raise IssueError("refusing to overwrite attempt directory")
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = args.output_dir.parent / (".stage-" + args.output_dir.name
                                      + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        write(stage / "qualification_solo.command.json", canonical(command))
        write(stage / "qualification_solo.authority.json", canonical(authority))
        os.rename(str(stage), str(args.output_dir))
    except BaseException:
        if stage.exists(): shutil.rmtree(str(stage))
        raise
    print(json.dumps({"status":"ISSUED_FRESH_TPCH_SOLO_DEBUG_QUALIFICATION_ONLY",
                      "attempt_id":args.new_attempt,
                      "collector_bundle_id":args.collector_bundle_id,
                      "adapter_bundle_id":args.adapter_bundle_id,
                      "expires_epoch":authority["expires_epoch"],
                      "formal_r0_authorized":False,
                      "accepted_round_authorized":False},sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (IssueError, OSError, ValueError, KeyError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
