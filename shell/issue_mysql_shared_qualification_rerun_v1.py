#!/usr/bin/env python3
"""Issue a fresh debug-only qualification attempt from a sealed predecessor.

This issuer changes only the qualification attempt identity and its derived
metric paths.  It cannot authorize capture, R0, formal replay, or accepted
rounds.  Publication is an atomic no-overwrite directory rename.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import time


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def valid_sidecar(path):
    sidecar = path.with_name(path.name + ".sha256")
    if path.is_symlink() or sidecar.is_symlink():
        return False
    if not path.is_file() or not sidecar.is_file():
        return False
    return sidecar.read_text(encoding="ascii") == "%s  %s\n" % (
        digest(path),
        path.name,
    )


def replace_attempt(value, old, new):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [replace_attempt(item, old, new) for item in value]
    if isinstance(value, dict):
        return {
            key: replace_attempt(item, old, new)
            for key, item in value.items()
        }
    return value


def write_exclusive(path, payload, mode=0o600):
    descriptor = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-command", required=True, type=Path)
    parser.add_argument("--old-authority", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--old-peer-attempt-id")
    parser.add_argument("--peer-attempt-id")
    parser.add_argument("--collector-bundle-id", required=True)
    parser.add_argument("--collector-manifest-sha256", required=True)
    parser.add_argument("--lifetime-seconds", type=int, default=1800)
    args = parser.parse_args()

    peer_args_complete = (
        args.old_peer_attempt_id is not None
        and args.peer_attempt_id is not None
    )
    if (
        len(args.attempt_id) < 32
        or not args.attempt_id.replace("-", "").isalnum()
        or ((args.old_peer_attempt_id is None) != (args.peer_attempt_id is None))
        or (
            peer_args_complete
            and (
                len(args.old_peer_attempt_id) < 32
                or len(args.peer_attempt_id) < 32
                or not args.old_peer_attempt_id.replace("-", "").isalnum()
                or not args.peer_attempt_id.replace("-", "").isalnum()
                or args.old_peer_attempt_id == args.peer_attempt_id
            )
        )
        or len(args.collector_bundle_id) != 64
        or len(args.collector_manifest_sha256) != 64
        or args.lifetime_seconds < 300
        or args.lifetime_seconds > 3600
        or not valid_sidecar(args.old_command)
        or not valid_sidecar(args.old_authority)
    ):
        raise RuntimeError("issuer input authority differs")
    old_command = json.loads(args.old_command.read_text(encoding="utf-8"))
    old_authority = json.loads(args.old_authority.read_text(encoding="utf-8"))
    old_attempt = old_command.get("qualification_attempt_id")
    token = args.token_file.read_text(encoding="utf-8").strip()
    if (
        old_command.get("schema_version")
        != "mysql_algorithm_c_debug_qualification_command_v1"
        or old_authority.get("schema_version")
        != "mysql_algorithm_c_main_agent_qualification_authority_v1"
        or old_authority.get("authorized_by") != "main_agent"
        or old_authority.get("qualification_attempt_id") != old_attempt
        or old_authority.get("command_spec_sha256") != digest(args.old_command)
        or old_authority.get("main_agent_token_sha256")
        != digest_bytes(token.encode("utf-8"))
        or any(
            value is not False
            for value in (
                old_command.get("capture_authorized"),
                old_command.get("formal_r0_authorized"),
                old_command.get("formal_authorized"),
                old_command.get("accepted_round_authorized"),
                old_authority.get("capture_authorized"),
                old_authority.get("formal_r0_authorized"),
                old_authority.get("formal_authorized"),
                old_authority.get("accepted_round_authorized"),
            )
        )
        or old_command.get("debug_only") is not True
        or old_authority.get("debug_only") is not True
        or not isinstance(old_attempt, str)
        or old_attempt == args.attempt_id
    ):
        raise RuntimeError("predecessor is not sealed debug qualification authority")

    command = replace_attempt(old_command, old_attempt, args.attempt_id)
    if peer_args_complete:
        paired_peer = old_command.get("paired_peer_workload")
        if (
            old_command.get("mode") != "qualification_paired"
            or not isinstance(paired_peer, dict)
            or paired_peer.get("qualification_attempt_id")
            != args.old_peer_attempt_id
            or args.old_peer_attempt_id not in json.dumps(
                paired_peer, sort_keys=True
            )
        ):
            raise RuntimeError("predecessor paired peer binding differs")
        command = replace_attempt(
            command, args.old_peer_attempt_id, args.peer_attempt_id
        )
    if (
        command.get("qualification_attempt_id") != args.attempt_id
        or old_attempt in json.dumps(command, sort_keys=True)
        or (
            peer_args_complete
            and (
                args.old_peer_attempt_id in json.dumps(command, sort_keys=True)
                or command.get("paired_peer_workload", {}).get(
                    "qualification_attempt_id"
                )
                != args.peer_attempt_id
            )
        )
        or command.get("mode") != old_command.get("mode")
        or command.get("bundle_id") != old_command.get("bundle_id")
    ):
        raise RuntimeError("derived command attempt binding differs")
    command_payload = canonical(command)
    now = time.time()
    authority = replace_attempt(old_authority, old_attempt, args.attempt_id)
    authority.update(
        {
            "command_spec_sha256": digest_bytes(command_payload),
            "not_before_epoch": now - 1.0,
            "expires_epoch": now + float(args.lifetime_seconds),
            "collector_bundle_id": args.collector_bundle_id,
            "collector_manifest_sha256": args.collector_manifest_sha256,
            "qualification_only": True,
        }
    )
    authority_payload = canonical(authority)

    if args.output_dir.exists():
        raise RuntimeError("output attempt directory already exists")
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = args.output_dir.parent / (
        ".stage-%s-%s" % (args.output_dir.name, secrets.token_hex(6))
    )
    stage.mkdir(mode=0o700)
    try:
        command_path = stage / "qualification_solo.command.json"
        authority_path = stage / "qualification_solo.authority.json"
        write_exclusive(command_path, command_payload)
        write_exclusive(
            command_path.with_name(command_path.name + ".sha256"),
            ("%s  %s\n" % (digest_bytes(command_payload), command_path.name)).encode(
                "ascii"
            ),
        )
        write_exclusive(authority_path, authority_payload)
        write_exclusive(
            authority_path.with_name(authority_path.name + ".sha256"),
            (
                "%s  %s\n"
                % (digest_bytes(authority_payload), authority_path.name)
            ).encode("ascii"),
        )
        if not valid_sidecar(command_path) or not valid_sidecar(authority_path):
            raise RuntimeError("staged sidecar verification failed")
        os.rename(stage, args.output_dir)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    print(
        json.dumps(
            {
                "status": "ISSUED_DEBUG_QUALIFICATION_ONLY",
                "attempt_id": args.attempt_id,
                "output_dir": str(args.output_dir),
                "command_spec_sha256": digest_bytes(command_payload),
                "authority_sha256": digest_bytes(authority_payload),
                "expires_epoch": authority["expires_epoch"],
                "formal_r0_authorized": False,
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
