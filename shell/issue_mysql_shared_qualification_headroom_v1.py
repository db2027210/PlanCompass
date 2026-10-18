#!/usr/bin/env python3
"""Issue one fresh paired debug qualification with a longer TPC-H guard.

This main-agent issuer never launches a workload.  It consumes two sealed
debug-only predecessor commands, rotates both reciprocal attempt identities,
changes only the TPC-H SQL guard from 240 to 360 seconds, and binds a new
collector identity plus a fresh short-lived token.  R0, formal replay, and
accepted-round authority remain explicitly false.
"""
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
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if (path.is_symlink() or sidecar.is_symlink() or not path.is_file()
            or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii") != "%s  %s\n" % (sha(path), path.name)):
        raise IssueError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise IssueError(label + " must be an object")
    return value


def replace(value, mapping):
    if isinstance(value, str):
        for old, new in mapping.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [replace(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: replace(item, mapping) for key, item in value.items()}
    return value


def validate_predecessor(command, authority, path, expected_own, expected_peer):
    flags = (
        "capture_authorized", "formal_r0_authorized", "formal_authorized",
        "accepted_round_authorized",
    )
    peer = command.get("paired_peer_workload") or {}
    if (
        command.get("schema_version") != "mysql_algorithm_c_debug_qualification_command_v1"
        or command.get("mode") != "qualification_paired"
        or command.get("debug_only") is not True
        or command.get("qualification_attempt_id") != expected_own
        or peer.get("qualification_attempt_id") != expected_peer
        or any(command.get(key) is not False for key in flags)
        or authority.get("schema_version") != "mysql_algorithm_c_main_agent_qualification_authority_v1"
        or authority.get("authorized_by") != "main_agent"
        or authority.get("debug_only") is not True
        or authority.get("qualification_attempt_id") != expected_own
        or authority.get("command_spec_sha256") != sha(path)
        or any(authority.get(key) is not False for key in flags)
    ):
        raise IssueError("predecessor debug authority differs: " + expected_own)


def token(path):
    path = Path(path)
    mode = path.stat().st_mode & 0o777
    value = path.read_text(encoding="ascii").strip()
    if path.is_symlink() or not path.is_file() or mode != 0o600 or len(value) < 48 or any(c.isspace() for c in value):
        raise IssueError("fresh token file differs")
    return value


def write_one(output, command, authority):
    output = Path(output)
    if output.exists():
        raise IssueError("refusing to overwrite attempt directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / (".stage-" + output.name + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        for name, value in (("qualification_solo.command.json", command),
                            ("qualification_solo.authority.json", authority)):
            raw = canonical(value); path = stage / name
            descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                os.write(descriptor, raw); os.fsync(descriptor)
            finally:
                os.close(descriptor)
            side = Path(str(path) + ".sha256")
            side.write_text("%s  %s\n" % (sha_bytes(raw), name), encoding="ascii")
            os.chmod(str(side), 0o600)
        os.rename(str(stage), str(output))
    except BaseException:
        if stage.exists(): shutil.rmtree(str(stage))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("--tpch-old-command", "--tpch-old-authority",
                 "--tpcc-old-command", "--tpcc-old-authority",
                 "--fresh-token-file", "--tpch-output-dir", "--tpcc-output-dir"):
        parser.add_argument(name, type=Path, required=True)
    parser.add_argument("--tpch-old-attempt", required=True)
    parser.add_argument("--tpcc-old-attempt", required=True)
    parser.add_argument("--tpch-new-attempt", required=True)
    parser.add_argument("--tpcc-new-attempt", required=True)
    parser.add_argument("--collector-bundle-id", required=True)
    parser.add_argument("--collector-manifest-sha256", required=True)
    parser.add_argument("--lifetime-seconds", type=int, default=1800)
    args = parser.parse_args()
    attempts = (args.tpch_old_attempt, args.tpcc_old_attempt,
                args.tpch_new_attempt, args.tpcc_new_attempt)
    if (any(len(value) < 32 or not value.replace("-", "").isalnum() for value in attempts)
            or len(set(attempts)) != 4
            or len(args.collector_bundle_id) != 64
            or len(args.collector_manifest_sha256) != 64
            or not 600 <= args.lifetime_seconds <= 3600):
        raise IssueError("issuer identity or TTL differs")
    h = load_sealed(args.tpch_old_command, "TPC-H old command")
    ha = load_sealed(args.tpch_old_authority, "TPC-H old authority")
    c = load_sealed(args.tpcc_old_command, "TPC-C old command")
    ca = load_sealed(args.tpcc_old_authority, "TPC-C old authority")
    validate_predecessor(h, ha, args.tpch_old_command, args.tpch_old_attempt, args.tpcc_old_attempt)
    validate_predecessor(c, ca, args.tpcc_old_command, args.tpcc_old_attempt, args.tpch_old_attempt)
    mapping = {args.tpch_old_attempt: args.tpch_new_attempt,
               args.tpcc_old_attempt: args.tpcc_new_attempt}
    h = replace(h, mapping); c = replace(c, mapping)
    duration_indexes = [index for index, value in enumerate(h.get("argv") or []) if value == "--duration-seconds=240"]
    native = h.get("native_sql_adapter_binding") or {}
    if len(duration_indexes) != 1 or native.get("workload_duration_seconds") != 240 or native.get("collector_sample_window_seconds") != 180:
        raise IssueError("TPC-H 240s guard binding differs")
    h["argv"][duration_indexes[0]] = "--duration-seconds=360"
    native["workload_duration_seconds"] = 360
    h["native_sql_adapter_binding"] = native
    if h.get("qualification_attempt_id") != args.tpch_new_attempt or c.get("qualification_attempt_id") != args.tpcc_new_attempt:
        raise IssueError("new own attempt binding differs")
    if (h.get("paired_peer_workload", {}).get("qualification_attempt_id") != args.tpcc_new_attempt
            or c.get("paired_peer_workload", {}).get("qualification_attempt_id") != args.tpch_new_attempt):
        raise IssueError("new reciprocal peer binding differs")
    fresh = token(args.fresh_token_file); now = time.time()
    def authority(old, command, own):
        value = replace(old, mapping)
        value.update({
            "qualification_attempt_id": own,
            "command_spec_sha256": sha_bytes(canonical(command)),
            "main_agent_token_sha256": sha_bytes(fresh.encode("ascii")),
            "not_before_epoch": now - 1.0,
            "expires_epoch": now + float(args.lifetime_seconds),
            "collector_bundle_id": args.collector_bundle_id,
            "collector_manifest_sha256": args.collector_manifest_sha256,
            "qualification_only": True,
            "debug_only": True,
            "capture_authorized": False,
            "formal_r0_authorized": False,
            "formal_authorized": False,
            "accepted_round_authorized": False,
        })
        return value
    ha = authority(ha, h, args.tpch_new_attempt); ca = authority(ca, c, args.tpcc_new_attempt)
    write_one(args.tpch_output_dir, h, ha); write_one(args.tpcc_output_dir, c, ca)
    print(json.dumps({
        "status": "ISSUED_PAIRED_DEBUG_QUALIFICATION_HEADROOM_ONLY",
        "tpch_attempt": args.tpch_new_attempt,
        "tpcc_attempt": args.tpcc_new_attempt,
        "tpch_guard_seconds": 360,
        "collector_sample_window_seconds": 180,
        "collector_bundle_id": args.collector_bundle_id,
        "expires_epoch": now + float(args.lifetime_seconds),
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (IssueError, OSError, ValueError, KeyError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
