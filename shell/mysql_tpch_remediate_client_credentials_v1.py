#!/usr/bin/env python3
"""Make the sealed TPC-H defaults file readable only by client UID 1008."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time


DOCKER = ("docker", "--host", "unix:///run/ra-docker-mysql-tpch-v1.sock")
class RemediationError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def state(path):
    value = Path(path).stat(follow_symlinks=False)
    return {
        "uid": value.st_uid,
        "gid": value.st_gid,
        "mode": "%04o" % stat.S_IMODE(value.st_mode),
    }


def atomic_json(path, value):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or sidecar.exists():
        raise RemediationError("refusing to overwrite credential authority")
    temporary = path.with_name(".%s.%s" % (path.name, secrets.token_hex(6)))
    temporary.write_bytes(canonical(value))
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(path))
    sidecar.write_text("%s  %s\n" % (sha256(path), path.name), encoding="ascii")
    os.chmod(str(sidecar), 0o600)


def inspect(identifier):
    completed = subprocess.run(
        list(DOCKER) + ["inspect", identifier],
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if completed.returncode:
        raise RemediationError("TPC-H client inspect failed")
    rows = json.loads(completed.stdout)
    if not isinstance(rows, list) or len(rows) != 1:
        raise RemediationError("TPC-H client inspect cardinality differs")
    return rows[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-dir", type=Path, required=True)
    parser.add_argument("--client-container-id", required=True)
    parser.add_argument("--client-pid", type=int, required=True)
    parser.add_argument("--client-start-ticks", type=int, required=True)
    parser.add_argument("--expected-file-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RemediationError("credential remediation requires root")
    directory = args.credentials_dir.resolve(strict=True)
    defaults = (directory / "mysql.cnf").resolve(strict=True)
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or defaults.is_symlink()
        or not defaults.is_file()
        or defaults.parent != directory
        or len(args.expected_file_sha256) != 64
        or any(character not in "0123456789abcdef" for character in args.expected_file_sha256)
        or sha256(defaults) != args.expected_file_sha256
    ):
        raise RemediationError("credential path/content identity differs")
    row = inspect(args.client_container_id)
    live_pid = int((row.get("State") or {}).get("Pid") or 0)
    stat_text = Path("/proc/%d/stat" % live_pid).read_text(encoding="ascii")
    start_ticks = int(stat_text.rsplit(")", 1)[1].strip().split()[19])
    if (
        row.get("Id") != args.client_container_id
        or (row.get("State") or {}).get("Running") is not True
        or live_pid != args.client_pid
        or start_ticks != args.client_start_ticks
        or (row.get("Config") or {}).get("User") != "1008:1008"
        or state(directory) != {"uid": 0, "gid": 0, "mode": "0700"}
        or state(defaults) != {"uid": 0, "gid": 0, "mode": "0600"}
    ):
        raise RemediationError("pre-remediation authority differs")
    before = {"directory": state(directory), "file": state(defaults)}
    os.chown(str(directory), 1008, 1008)
    os.chmod(str(directory), 0o700)
    os.chown(str(defaults), 1008, 1008)
    os.chmod(str(defaults), 0o400)
    after = {"directory": state(directory), "file": state(defaults)}
    if (
        after
        != {
            "directory": {"uid": 1008, "gid": 1008, "mode": "0700"},
            "file": {"uid": 1008, "gid": 1008, "mode": "0400"},
        }
        or sha256(defaults) != args.expected_file_sha256
    ):
        raise RemediationError("post-remediation credential identity differs")
    result = {
        "schema_version": "mysql_tpch_client_private_credentials_remediation_v1",
        "status": "PASS_CLIENT_PRIVATE_READABLE_CONTENT_UNCHANGED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "credentials_directory": str(directory),
        "defaults_file": str(defaults),
        "defaults_file_sha256_before_after": args.expected_file_sha256,
        "client_container_id": args.client_container_id,
        "client_pid": live_pid,
        "client_start_ticks": start_ticks,
        "before": before,
        "after": after,
        "container_restarted": False,
        "database_mutated": False,
        "workload_started": False,
        "observed_epoch": time.time(),
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    result["authority_sha256"] = hashlib.sha256(canonical(result)).hexdigest()
    atomic_json(args.output, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (RemediationError, OSError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
