#!/usr/bin/env python3
"""Read-only exact-tree verifier for a remotely published bundle.

Compatible with Python 3.6.  The bundle manifest is the authority for every
payload file.  The manifest and its optional exact SHA-256 sidecar are the
only regular files allowed outside that map.  Symlinks and special filesystem
entries are rejected.
"""

from __future__ import print_function

import hashlib
import json
import os
import stat
import sys


class VerificationError(RuntimeError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify(root, expected_bundle_id, allow_staging_name=False):
    root = os.path.abspath(root)
    if (
        os.path.basename(root) != expected_bundle_id
        and not allow_staging_name
    ):
        raise VerificationError("bundle directory/content address differs")
    manifest_path = os.path.join(root, "bundle_manifest.json")
    if os.path.islink(manifest_path) or not os.path.isfile(manifest_path):
        raise VerificationError("bundle manifest is absent or symlinked")
    with open(manifest_path, "r") as stream:
        manifest = json.load(stream)
    if manifest.get("bundle_id") != expected_bundle_id:
        raise VerificationError("bundle manifest identity differs")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise VerificationError("bundle file map is absent")

    sidecar_relative = "bundle_manifest.json.sha256"
    sidecar_path = os.path.join(root, sidecar_relative)
    sidecar_unmapped = sidecar_relative not in expected
    if os.path.lexists(sidecar_path) and sidecar_unmapped:
        if os.path.islink(sidecar_path) or not os.path.isfile(sidecar_path):
            raise VerificationError("bundle manifest sidecar is not regular")
        with open(sidecar_path, "r") as stream:
            sidecar_text = stream.read()
        required = "%s  bundle_manifest.json\n" % sha256(manifest_path)
        if sidecar_text != required:
            raise VerificationError("bundle manifest sidecar differs")

    actual = {}
    for directory, directory_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        for name in directory_names:
            path = os.path.join(directory, name)
            mode = os.lstat(path).st_mode
            if not stat.S_ISDIR(mode):
                raise VerificationError("non-directory entry: " + path)
        for name in file_names:
            path = os.path.join(directory, name)
            mode = os.lstat(path).st_mode
            if not stat.S_ISREG(mode):
                raise VerificationError("non-regular file entry: " + path)
            relative = os.path.relpath(path, root)
            if relative == "bundle_manifest.json" or (
                relative == sidecar_relative and sidecar_unmapped
            ):
                continue
            actual[relative] = sha256(path)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        additional = sorted(set(actual) - set(expected))
        changed = sorted(
            relative
            for relative in set(expected) & set(actual)
            if expected[relative] != actual[relative]
        )
        raise VerificationError(
            "bundle closure differs missing=%r additional=%r changed=%r"
            % (missing, additional, changed)
        )
    return len(actual)


def main():
    if len(sys.argv) not in (3, 4):
        raise VerificationError(
            "usage: verify_remote_content_addressed_bundle.py "
            "ROOT BUNDLE_ID [--allow-staging-name]"
        )
    allow_staging_name = (
        len(sys.argv) == 4 and sys.argv[3] == "--allow-staging-name"
    )
    if len(sys.argv) == 4 and not allow_staging_name:
        raise VerificationError("unknown verifier argument")
    count = verify(sys.argv[1], sys.argv[2], allow_staging_name)
    print("REMOTE_BUNDLE_EXACT_PASS %s %d" % (sys.argv[2], count))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (IOError, OSError, ValueError, VerificationError) as error:
        sys.stderr.write("REMOTE_BUNDLE_VERIFY_FAILED: %s\n" % error)
        sys.exit(2)
