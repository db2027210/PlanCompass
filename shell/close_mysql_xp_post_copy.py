#!/usr/bin/env python3
"""Fail-closed controller for the MySQL XP template-v2 post-copy closure.

Default mode performs only read-only completion checks.  Mutation requires
``--execute`` plus literal confirmation of host, source and target.  The
controller never starts MySQL, changes readiness, deletes template files, or
places credentials on a command line.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence


DEFAULT_SOURCE = Path("/disk2/resource_adaptive/unified_formal_20260721_v1/templates/hybench")
DEFAULT_HOST = "anonymous@192.0.2.4"
DEFAULT_TARGET = Path(
    "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp/template_v2"
)
DEFAULT_RSYNC_PID = 83927
AUTHORITATIVE_MANIFEST_SHA256 = (
    "fd7c9918890fe6eea33f900da05a8d2825e204bc3f3cbf90505ddc43b98b52fa"
)
HOST_PATTERN = re.compile(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+\Z")


class XPPostCopyError(RuntimeError):
    """The closure cannot safely advance."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class SubprocessRunner:
    def run(self, args: Sequence[str], *, input_bytes: bytes | None = None) -> CommandResult:
        completed = subprocess.run(
            list(args), input=input_bytes, capture_output=True, check=False
        )
        return CommandResult(
            completed.returncode,
            completed.stdout.decode("utf-8", errors="replace"),
            completed.stderr.decode("utf-8", errors="replace"),
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _regular_file(path: Path, label: str) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise XPPostCopyError(f"{label} must be an absolute non-symlink file")
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise XPPostCopyError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(mode):
        raise XPPostCopyError(f"{label} is not a regular file")
    return path.read_bytes()


def _validate_inputs(source: Path, host: str, target: Path) -> None:
    if not source.is_absolute() or source == Path("/"):
        raise XPPostCopyError("source must be a specific absolute directory")
    if source.is_symlink() or not source.is_dir():
        raise XPPostCopyError("source must be an existing non-symlink directory")
    if not target.is_absolute() or target == Path("/") or "\n" in str(target):
        raise XPPostCopyError("target must be a specific absolute path")
    if HOST_PATTERN.fullmatch(host) is None:
        raise XPPostCopyError("host must be an explicit user@host value")


def _pid_is_live(pid: int) -> bool:
    if pid <= 0:
        raise XPPostCopyError("rsync PID must be positive")
    path = Path(f"/proc/{pid}/stat")
    try:
        fields = path.read_text(encoding="ascii").split()
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError) as exc:
        raise XPPostCopyError("cannot inspect original rsync PID") from exc
    return len(fields) >= 3 and fields[2] not in {"Z", "X"}


def _ssh(host: str, remote_command: str) -> tuple[str, ...]:
    return (
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "--", host, remote_command,
    )


def _completion_probe(source: Path, host: str, target: Path) -> tuple[str, ...]:
    return (
        "rsync", "-a", "--dry-run", "--checksum", "--itemize-changes",
        "--out-format=%i|%n%L", f"{source / 'datadir'}/", f"{host}:{target}/datadir/",
    )


def _atomic_remote_publish_command(
    destination: Path, expected_sha256: str, mode: str,
) -> str:
    dst = shlex.quote(str(destination))
    expected = shlex.quote(expected_sha256)
    # Existing authoritative bytes are retained.  A mismatching existing file
    # fails instead of being overwritten.  rm only cleans a private partial.
    return "\n".join((
        "set -eu",
        "umask 077",
        f"dst={dst}",
        f"expected={expected}",
        'test ! -L "$dst"',
        'if test -e "$dst"; then',
        '  test -f "$dst"',
        '  test "$(sha256sum "$dst" | awk \'{print $1}\')" = "$expected"',
        "  dd of=/dev/null status=none",
        "  exit 0",
        "fi",
        'tmp="${dst}.partial.$$"',
        'test ! -e "$tmp"',
        'trap \'rm -f -- "$tmp"\' EXIT HUP INT TERM',
        'dd of="$tmp" status=none',
        'test "$(sha256sum "$tmp" | awk \'{print $1}\')" = "$expected"',
        f'chmod {mode} "$tmp"',
        'mv -T -- "$tmp" "$dst"',
        "trap - EXIT HUP INT TERM",
    ))


def _seal_command(target: Path) -> str:
    root = shlex.quote(str(target))
    return "\n".join((
        "set -eu",
        f"root={root}",
        'test -d "$root/datadir"',
        'test -f "$root/database_manifest.json"',
        'test -f "$root/database_manifest.json.sha256"',
        'find "$root" -xdev -type f -exec chmod 0444 {} +',
        'find "$root" -xdev -type d -exec chmod 0555 {} +',
    ))


def _verifier_command(target: Path) -> tuple[str, ...]:
    return _ssh(
        DEFAULT_HOST,  # caller replaces the host element before use
        "python3 - --template-root " + shlex.quote(str(target)),
    )


def _atomic_local_evidence(directory: Path, payload: bytes) -> Mapping[str, str]:
    if directory.exists() or directory.is_symlink():
        raise XPPostCopyError("evidence directory already exists")
    if not directory.is_absolute():
        raise XPPostCopyError("evidence directory must be absolute")
    directory.mkdir(parents=True, mode=0o700)
    output = directory / "xp_post_copy_verification.json"
    sidecar = output.with_name(output.name + ".sha256")
    digest = _sha256(payload)
    temporary = output.with_name(output.name + f".partial.{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise XPPostCopyError("short verification evidence write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, output)
    line = f"{digest}  {output.name}\n".encode("ascii")
    side_descriptor = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.write(side_descriptor, line) != len(line):
            raise XPPostCopyError("short evidence sidecar write")
        os.fsync(side_descriptor)
    finally:
        os.close(side_descriptor)
    return {"json": str(output), "sha256": digest, "sidecar": str(sidecar)}


def close_post_copy(
    *,
    source: Path,
    host: str,
    target: Path,
    rsync_pid: int,
    execute: bool,
    confirm_host: str | None = None,
    confirm_source: str | None = None,
    confirm_target: str | None = None,
    evidence_dir: Path | None = None,
    runner: Any | None = None,
    pid_is_live: Callable[[int], bool] = _pid_is_live,
    verifier_path: Path | None = None,
) -> Mapping[str, Any]:
    source = source.absolute()
    target = Path(str(target))
    _validate_inputs(source, host, target)
    if pid_is_live(rsync_pid):
        raise XPPostCopyError(f"original rsync PID is still live: {rsync_pid}")
    command_runner = runner or SubprocessRunner()
    manifest_path = source / "database_manifest.json"
    verifier = verifier_path or (
        Path(__file__).resolve().parent / "verify_mysql_xp_template_copy.py"
    )
    manifest_bytes = _regular_file(manifest_path, "authoritative manifest")
    if _sha256(manifest_bytes) != AUTHORITATIVE_MANIFEST_SHA256:
        raise XPPostCopyError("authoritative manifest SHA-256 mismatch")
    verifier_bytes = _regular_file(verifier.absolute(), "XP copy verifier")

    probe = _completion_probe(source, host, target)
    checked = command_runner.run(probe)
    if checked.returncode != 0:
        raise XPPostCopyError(
            f"checksum rsync completion probe failed with rc={checked.returncode}"
        )
    differences = [line for line in checked.stdout.splitlines() if line.strip()]
    if differences:
        raise XPPostCopyError(
            f"checksum rsync completion probe found {len(differences)} differences"
        )

    plan = {
        "schema_version": "mysql_xp_post_copy_closure_v1",
        "mode": "execute" if execute else "dry_run",
        "source": str(source),
        "host": host,
        "target": str(target),
        "original_rsync_pid": rsync_pid,
        "original_rsync_exit_code": "unavailable_orphan_not_used",
        "equivalent_completion_evidence": {
            "method": "rsync_archive_dry_run_checksum_itemize_zero_diff",
            "returncode": checked.returncode,
            "difference_count": 0,
        },
        "mutations_performed": False,
        "starts_mysql": False,
        "changes_readiness": False,
        "deletes_template_files": False,
    }
    if not execute:
        return plan
    if (
        confirm_host != host
        or confirm_source != str(source)
        or confirm_target != str(target)
    ):
        raise XPPostCopyError(
            "--execute requires exact --confirm-host/--confirm-source/--confirm-target"
        )
    if evidence_dir is None:
        raise XPPostCopyError("--execute requires an explicit evidence directory")

    manifest_destination = target / "database_manifest.json"
    manifest_publish = command_runner.run(
        _ssh(host, _atomic_remote_publish_command(
            manifest_destination, AUTHORITATIVE_MANIFEST_SHA256, "0444"
        )),
        input_bytes=manifest_bytes,
    )
    if manifest_publish.returncode != 0:
        raise XPPostCopyError(
            f"atomic manifest publication failed with rc={manifest_publish.returncode}"
        )
    sidecar_bytes = (
        f"{AUTHORITATIVE_MANIFEST_SHA256}  database_manifest.json\n".encode("ascii")
    )
    sidecar_publish = command_runner.run(
        _ssh(host, _atomic_remote_publish_command(
            target / "database_manifest.json.sha256", _sha256(sidecar_bytes), "0444"
        )),
        input_bytes=sidecar_bytes,
    )
    if sidecar_publish.returncode != 0:
        raise XPPostCopyError(
            f"atomic sidecar publication failed with rc={sidecar_publish.returncode}"
        )
    sealed = command_runner.run(_ssh(host, _seal_command(target)))
    if sealed.returncode != 0:
        raise XPPostCopyError(f"read-only sealing failed with rc={sealed.returncode}")

    verification_args = list(_verifier_command(target))
    verification_args[6] = host
    verified = command_runner.run(tuple(verification_args), input_bytes=verifier_bytes)
    verification_payload = verified.stdout.encode("utf-8")
    try:
        verification_document = json.loads(verified.stdout)
    except json.JSONDecodeError as exc:
        raise XPPostCopyError("remote verifier did not return JSON") from exc
    if verified.returncode != 0 or verification_document.get("status") != "PASS":
        raise XPPostCopyError(
            f"remote verifier failed with rc={verified.returncode} "
            f"status={verification_document.get('status')!r}"
        )
    evidence = _atomic_local_evidence(evidence_dir.absolute(), verification_payload)
    return {
        **plan,
        "mutations_performed": True,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "remote_verification_status": "PASS",
        "verification_evidence": evidence,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--rsync-pid", type=int, default=DEFAULT_RSYNC_PID)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-host")
    parser.add_argument("--confirm-source")
    parser.add_argument("--confirm-target")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        result = close_post_copy(
            source=args.source,
            host=args.host,
            target=args.target,
            rsync_pid=args.rsync_pid,
            execute=args.execute,
            confirm_host=args.confirm_host,
            confirm_source=args.confirm_source,
            confirm_target=args.confirm_target,
            evidence_dir=args.evidence_dir,
        )
    except XPPostCopyError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"status": "PASS", **result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
