#!/usr/bin/env python3
"""Atomically finalize a verified same-filesystem ingress bundle as root.

The operation is no-replace and idempotent.  It never launches a workload and
never changes an existing destination.  Python 3.6 compatible for node04.
"""
import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re


class PublishError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verify(root, expected_id):
    root = Path(root)
    if root.is_symlink() or not root.is_dir() or root.name != expected_id:
        raise PublishError("bundle root identity differs")
    manifest_path = root / "bundle_manifest.json"
    sidecar = root / "bundle_manifest.json.sha256"
    if manifest_path.is_symlink() or sidecar.is_symlink():
        raise PublishError("manifest symlink prohibited")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    manifest_sha = digest(manifest_path)
    if (
        bundle_id != expected_id
        or hashlib.sha256(canonical(unsigned)).hexdigest() != expected_id
        or sidecar.read_text(encoding="ascii")
        != manifest_sha + "  bundle_manifest.json\n"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise PublishError("bundle manifest identity differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise PublishError("bundle file closure is absent")
    expected = set(files) | {"bundle_manifest.json", "bundle_manifest.json.sha256"}
    actual = set()
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise PublishError("bundle symlink prohibited")
        relative = str(candidate.relative_to(root))
        if candidate.is_file():
            actual.add(relative)
            if candidate.stat().st_mode & 0o222:
                raise PublishError("writable bundle file: " + relative)
        elif not candidate.is_dir():
            raise PublishError("special bundle entry: " + relative)
    if actual != expected:
        raise PublishError("bundle exact file set differs")
    for relative, expected_sha in files.items():
        if not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha)):
            raise PublishError("bundle file hash malformed")
        if digest(root / relative) != expected_sha:
            raise PublishError("bundle file hash differs: " + relative)
    return manifest_sha, len(files)


def rename_noreplace(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    arguments = (-100, os.fsencode(str(source)), -100,
                 os.fsencode(str(destination)), 1)
    if function is not None:
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                             ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(*arguments)
    else:
        # CentOS 7's glibc predates the exported renameat2 wrapper although
        # the x86_64 kernel syscall is available.  Fail closed on any other
        # architecture instead of falling back to replacing os.rename().
        if os.uname().machine != "x86_64":
            raise PublishError("renameat2 is unavailable")
        syscall = libc.syscall
        syscall.restype = ctypes.c_long
        result = syscall(316, *arguments)
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(str(destination))
        raise PublishError("renameat2 failed errno=%d" % error)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ingress", type=Path, required=True)
    parser.add_argument("--destination-parent", type=Path, required=True)
    parser.add_argument("--bundle-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{64}", args.bundle_id or "") is None:
        raise PublishError("bundle ID malformed")
    ingress = args.ingress.resolve(strict=True)
    destination_parent = args.destination_parent.resolve(strict=True)
    destination = destination_parent / args.bundle_id
    if ingress.stat().st_dev != destination_parent.stat().st_dev:
        raise PublishError("ingress and destination are not on one filesystem")
    ingress_sha, file_count = verify(ingress, args.bundle_id)
    if destination.exists() or destination.is_symlink():
        destination_sha, destination_count = verify(destination, args.bundle_id)
        if (destination_sha, destination_count) != (ingress_sha, file_count):
            raise PublishError("existing destination content differs")
        print(json.dumps({"status": "ALREADY_PUBLISHED_EXACT_IDEMPOTENT",
                          "bundle_id": args.bundle_id,
                          "destination": str(destination),
                          "manifest_sha256": destination_sha,
                          "file_count": destination_count}, sort_keys=True))
        return
    lock = destination_parent / (".publish-lock-" + args.bundle_id)
    try:
        os.mkdir(str(lock), 0o700)
    except FileExistsError:
        raise PublishError("publication lock is busy")
    try:
        if destination.exists() or destination.is_symlink():
            raise PublishError("destination appeared while locked")
        rename_noreplace(ingress, destination)
        descriptor = os.open(str(destination_parent), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        final_sha, final_count = verify(destination, args.bundle_id)
        if (final_sha, final_count) != (ingress_sha, file_count):
            raise PublishError("visible destination verification differs")
    finally:
        os.rmdir(str(lock))
    print(json.dumps({"status": "PUBLISHED_ATOMIC_NOREPLACE_VERIFIED",
                      "bundle_id": args.bundle_id,
                      "destination": str(destination),
                      "manifest_sha256": final_sha,
                      "file_count": final_count}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, PublishError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
