#!/usr/bin/env python3
"""Read-only Python 3.6 verifier for one content-addressed bundle directory."""

import hashlib
import json
from pathlib import Path
import sys


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    if len(sys.argv) != 2:
        raise RuntimeError("usage: verify_remote_content_bundle_v1.py BUNDLE")
    bundle = Path(sys.argv[1]).resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    bundle_id = manifest.get("bundle_id")
    allowed_names = (bundle_id, ".stage-" + str(bundle_id))
    if bundle.name not in allowed_names or hashlib.sha256(canonical(identity)).hexdigest() != bundle_id:
        raise RuntimeError("bundle identity differs")
    expected = manifest.get("files")
    if not isinstance(expected, dict):
        raise RuntimeError("manifest files map differs")
    actual = {}
    for path in bundle.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("symlink prohibited: " + str(path))
        if path.is_file():
            relative = str(path.relative_to(bundle))
            if relative not in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
                actual[relative] = digest(path)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        mismatched = sorted(key for key in set(actual) & set(expected) if actual[key] != expected[key])
        raise RuntimeError("bundle closure differs missing=%r extra=%r mismatched=%r" % (missing, extra, mismatched))
    print(json.dumps({"status": "PASS_EXACT_CONTENT_ADDRESSED_BUNDLE", "bundle_id": bundle_id, "staged": bundle.name != bundle_id, "manifest_sha256": digest(manifest_path), "file_count": len(actual)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
