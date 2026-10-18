#!/usr/bin/env python3
"""Transactionally publish one immutable bundle to a remote same-FS root.

The program performs no data-plane action.  It copies into a unique direct
sibling staging directory, verifies the exact manifest closure remotely, obtains an
atomic per-bundle mkdir lock, renames without replacing an existing bundle,
fsyncs the destination directory, and verifies the visible result again.
Existing exact content is an idempotent success; differing content is always
fail-closed and is never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
from typing import Sequence


class PublishError(RuntimeError):
    pass


ROOT = Path(__file__).resolve().parents[1]
REMOTE_VERIFIER = ROOT / "shell/verify_remote_content_addressed_bundle.py"


def run(
    argv: Sequence[str],
    *,
    input_text: str | None = None,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(argv),
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishError(f"command failed or timed out: {argv[0]}: {exc}") from exc
    if result.returncode:
        detail = ((result.stderr or "") + "\n" + (result.stdout or ""))[-4000:]
        raise PublishError(
            f"command failed rc={result.returncode}: {argv[0]}: {detail.strip()}"
        )
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_local(bundle: Path) -> tuple[str, int]:
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PublishError("local bundle manifest is absent or symlinked")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_id = manifest.get("bundle_id")
    expected = manifest.get("files")
    if (
        not isinstance(bundle_id, str)
        or len(bundle_id) != 64
        or any(value not in "0123456789abcdef" for value in bundle_id)
        or bundle.name != bundle_id
        or not isinstance(expected, dict)
        or not expected
    ):
        raise PublishError("local bundle identity/file map differs")
    sidecar_relative = "bundle_manifest.json.sha256"
    sidecar_path = bundle / sidecar_relative
    sidecar_unmapped = sidecar_relative not in expected
    if sidecar_path.exists() and sidecar_unmapped:
        if sidecar_path.is_symlink() or not sidecar_path.is_file():
            raise PublishError("local bundle manifest sidecar is not regular")
        required = f"{sha256(manifest_path)}  bundle_manifest.json\n"
        if sidecar_path.read_text(encoding="ascii") != required:
            raise PublishError("local bundle manifest sidecar differs")
    actual: dict[str, str] = {}
    for path in bundle.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise PublishError(f"local bundle contains a special entry: {path}")
        relative = path.relative_to(bundle).as_posix()
        if relative == "bundle_manifest.json" or (
            relative == sidecar_relative and sidecar_unmapped
        ):
            continue
        actual[relative] = sha256(path)
    if actual != expected:
        raise PublishError("local bundle exact closure differs")
    return bundle_id, len(actual)


REMOTE_PREPARE = r"""
from __future__ import print_function
import os
import sys

parent, stage = sys.argv[1:3]
if not os.path.isabs(parent) or not os.path.isabs(stage):
    raise SystemExit("publication paths must be absolute")
if os.path.dirname(stage) != parent:
    raise SystemExit("staging directory is not a direct destination sibling")
if os.path.lexists(stage):
    raise SystemExit("unique staging directory already exists")
if not os.path.isdir(parent) or os.path.islink(parent):
    raise SystemExit("destination parent is absent or symlinked")
descriptor = os.open(parent, os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
print("REMOTE_DIRECT_STAGE_READY " + stage)
"""


REMOTE_PROBE_FINAL = r"""
from __future__ import print_function
import os
import sys

final = sys.argv[1]
if not os.path.isabs(final):
    raise SystemExit("visible destination must be absolute")
if not os.path.lexists(final):
    print("REMOTE_FINAL_ABSENT " + final)
elif os.path.isdir(final) and not os.path.islink(final):
    print("REMOTE_FINAL_PRESENT_DIRECTORY " + final)
else:
    raise SystemExit("visible destination is not a real directory")
"""


REMOTE_PUBLISH = r"""
from __future__ import print_function
import os
import sys

stage_child, final, lock = sys.argv[1:4]
parent = os.path.dirname(final)
if (
    not os.path.isabs(stage_child)
    or not os.path.isabs(final)
    or not os.path.isabs(lock)
    or os.path.dirname(lock) != parent
    or os.path.dirname(stage_child) != parent
):
    raise SystemExit("publication path relationship differs")
os.mkdir(lock, 0o700)
published = False
try:
    if os.path.lexists(final):
        if not os.path.isdir(final) or os.path.islink(final):
            raise SystemExit("visible destination is not a real directory")
        print("REMOTE_BUNDLE_ALREADY_VISIBLE " + final)
    else:
        os.rename(stage_child, final)
        published = True
        descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        print("REMOTE_BUNDLE_ATOMICALLY_PUBLISHED " + final)
finally:
    os.rmdir(lock)
"""


def remote_python(host: str, arguments: Sequence[str], source: str) -> str:
    result = run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            host,
            "python3",
            "-",
            *arguments,
        ],
        input_text=source,
        timeout=120,
    )
    return result.stdout


def remote_verify(
    host: str, path: str, bundle_id: str, *, allow_staging_name: bool = False,
) -> str:
    return remote_python(
        host,
        [path, bundle_id]
        + (["--allow-staging-name"] if allow_staging_name else []),
        REMOTE_VERIFIER.read_text(encoding="utf-8"),
    )


def publish(bundle: Path, host: str, destination_parent: str) -> dict[str, object]:
    bundle = bundle.resolve(strict=True)
    bundle_id, file_count = verify_local(bundle)
    if not destination_parent.startswith("/"):
        raise PublishError("remote destination parent must be absolute")
    final = f"{destination_parent}/{bundle_id}"
    final_probe = remote_python(host, [final], REMOTE_PROBE_FINAL).strip()
    if final_probe == "REMOTE_FINAL_PRESENT_DIRECTORY " + final:
        visible_verification = remote_verify(host, final, bundle_id).strip()
        return {
            "schema_version": "remote_content_addressed_publication_v1",
            "status": "PASS_ALREADY_PRESENT_EXACT",
            "host": host,
            "destination": final,
            "bundle_id": bundle_id,
            "file_count": file_count,
            "final_probe": final_probe,
            "staged_verification": None,
            "publication": "REMOTE_BUNDLE_ALREADY_VISIBLE " + final,
            "visible_verification": visible_verification,
            "idempotent_no_write": True,
            "data_plane_started": False,
        }
    if final_probe != "REMOTE_FINAL_ABSENT " + final:
        raise PublishError("remote visible-destination probe differs")
    token = secrets.token_hex(12)
    stage_child = f"{destination_parent}/.publish-{bundle_id[:16]}-{token}"
    lock = f"{destination_parent}/.publish-lock-{bundle_id}"
    remote_python(host, [destination_parent, stage_child], REMOTE_PREPARE)
    run(
        [
            "scp",
            "-p",
            "-r",
            str(bundle),
            f"{host}:{stage_child}",
        ],
        timeout=1800,
    )
    staged_verification = remote_verify(
        host, stage_child, bundle_id, allow_staging_name=True
    ).strip()
    publication = remote_python(
        host, [stage_child, final, lock], REMOTE_PUBLISH
    ).strip()
    visible_verification = remote_verify(host, final, bundle_id).strip()
    return {
        "schema_version": "remote_content_addressed_publication_v1",
        "status": "PASS",
        "host": host,
        "destination": final,
        "bundle_id": bundle_id,
        "file_count": file_count,
        "final_probe": final_probe,
        "staged_verification": staged_verification,
        "publication": publication,
        "visible_verification": visible_verification,
        "idempotent_no_write": False,
        "data_plane_started": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--host", required=True)
    parser.add_argument("--destination-parent", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            publish(args.bundle, args.host, args.destination_parent),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, PublishError) as exc:
        print(f"REMOTE_BUNDLE_PUBLICATION_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2)
