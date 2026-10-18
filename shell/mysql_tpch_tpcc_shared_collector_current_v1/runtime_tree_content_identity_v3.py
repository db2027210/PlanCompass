#!/usr/bin/env python3
"""Directory-aware portable runtime tree authority (Python 3.6 compatible).

V3 is the sole exact full-tree schema for new Algorithm C successors.  Unlike
V2 it binds every relative directory path and directory mode, including empty
directories.  Regular files bind path, size, mode, and SHA-256.  Symlinks bind
path and the literal link target without following the link; Linux symlink
mode is deliberately excluded because it is not a portable permission.

Source host, source root, timestamps, and runtime labels are provenance only
and never participate in the portable content digest.
"""

from __future__ import print_function

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


CONTENT_SCHEMA = "mysql_algorithm_c_runtime_tree_content_identity_v3"
PORTABLE_MANIFEST_SCHEMA = "mysql_algorithm_c_runtime_tree_manifest_v3"
INVENTORY_SCHEMA = "remote_runtime_tree_inventory_v2"
PUBLICATION_SCHEMA = "mysql_algorithm_c_remote_runtime_manifest_v1"
READBACK_SCHEMA = "mysql_algorithm_c_runtime_tree_readback_v3"
ALGORITHM = "sha256_canonical_relative_tree_entries_v3"


class RuntimeTreeError(RuntimeError):
    pass


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


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _lower_hex(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _relative(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeTreeError("runtime entry path is absent or invalid")
    if value.startswith("/") or "\\" in value:
        raise RuntimeTreeError("runtime entry path is not normalized: %r" % value)
    pieces = value.split("/")
    if any(piece in ("", ".", "..") for piece in pieces):
        raise RuntimeTreeError("runtime entry path is unsafe: %r" % value)
    if os.path.normpath(value).replace(os.sep, "/") != value:
        raise RuntimeTreeError("runtime entry path is not canonical: %r" % value)
    return value


def _mode(value, label):
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > 0o7777
    ):
        raise RuntimeTreeError("%s mode is invalid" % label)
    return value


def normalize_entries(entries):
    if not isinstance(entries, list):
        raise RuntimeTreeError("runtime entries must be a list")
    result = []
    seen = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise RuntimeTreeError("runtime entry must be an object")
        relative = _relative(raw.get("path"))
        if relative in seen:
            raise RuntimeTreeError("duplicate runtime entry: %s" % relative)
        seen.add(relative)
        entry_type = raw.get("type")
        if entry_type == "file":
            if set(raw) != {"path", "type", "size", "mode", "sha256"}:
                raise RuntimeTreeError("file metadata differs: %s" % relative)
            size = raw.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise RuntimeTreeError("file size invalid: %s" % relative)
            digest = raw.get("sha256")
            if not _lower_hex(digest):
                raise RuntimeTreeError("file SHA-256 invalid: %s" % relative)
            result.append(
                {
                    "path": relative,
                    "type": "file",
                    "size": size,
                    "mode": _mode(raw.get("mode"), relative),
                    "sha256": digest,
                }
            )
        elif entry_type == "symlink":
            if set(raw) != {"path", "type", "target"}:
                raise RuntimeTreeError("symlink metadata differs: %s" % relative)
            target = raw.get("target")
            if not isinstance(target, str) or not target or "\x00" in target:
                raise RuntimeTreeError("symlink target invalid: %s" % relative)
            result.append(
                {"path": relative, "type": "symlink", "target": target}
            )
        elif entry_type == "directory":
            if set(raw) != {"path", "type", "mode"}:
                raise RuntimeTreeError(
                    "directory metadata differs: %s" % relative
                )
            result.append(
                {
                    "path": relative,
                    "type": "directory",
                    "mode": _mode(raw.get("mode"), relative),
                }
            )
        else:
            raise RuntimeTreeError(
                "special or unknown runtime entry: %s" % relative
            )
    return sorted(result, key=lambda row: row["path"])


def content_identity(entries):
    normalized = normalize_entries(entries)
    payload = {
        "schema_version": CONTENT_SCHEMA,
        "file_count": sum(row["type"] == "file" for row in normalized),
        "symlink_count": sum(row["type"] == "symlink" for row in normalized),
        "directory_count": sum(
            row["type"] == "directory" for row in normalized
        ),
        "total_file_bytes": sum(
            row["size"] for row in normalized if row["type"] == "file"
        ),
        "entries": normalized,
    }
    return {
        "algorithm": ALGORITHM,
        "tree_content_sha256": sha_bytes(canonical(payload)),
        "identity": payload,
    }


def validate_inventory(inventory):
    if not isinstance(inventory, dict):
        raise RuntimeTreeError("runtime inventory must be an object")
    if inventory.get("schema_version") != INVENTORY_SCHEMA:
        raise RuntimeTreeError("runtime inventory schema differs")
    root = inventory.get("root")
    if not isinstance(root, str) or not root.startswith("/") or root == "/":
        raise RuntimeTreeError("runtime inventory source root is invalid")
    portable = content_identity(inventory.get("entries"))
    for field in (
        "file_count",
        "symlink_count",
        "directory_count",
        "total_file_bytes",
    ):
        value = inventory.get(field)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value != portable["identity"][field]
        ):
            raise RuntimeTreeError("runtime inventory %s differs" % field)
    return portable


def validate_publication(document):
    if not isinstance(document, dict):
        raise RuntimeTreeError("runtime publication must be an object")
    inventory = document.get("inventory")
    if (
        document.get("schema_version") != PUBLICATION_SCHEMA
        or document.get("formal_scope") != "mysql_only"
        or document.get("cross_engine_comparison_prohibited") is not True
        or document.get("launch_authorized") is not False
        or document.get("remote_mutation_performed") is not False
    ):
        raise RuntimeTreeError("runtime publication scope/authority differs")
    portable = validate_inventory(inventory)
    legacy = sha_bytes(
        (
            json.dumps(
                inventory, ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n"
        ).encode("utf-8")
    )
    if (
        document.get("tree_sha256") != legacy
        or document.get("remote_root") != inventory.get("root")
        or not isinstance(document.get("host"), str)
        or not document.get("host")
    ):
        raise RuntimeTreeError("runtime publication provenance differs")
    result = dict(portable)
    result.update(
        {
            "legacy_location_bound_tree_sha256": legacy,
            "source_root": inventory["root"],
            "source_host": document["host"],
        }
    )
    return result


def portable_manifest(entries, source_provenance, artifacts=None):
    portable = content_identity(entries)
    document = {
        "schema_version": PORTABLE_MANIFEST_SCHEMA,
        "algorithm": portable["algorithm"],
        "tree_content_sha256": portable["tree_content_sha256"],
        "content_identity": portable["identity"],
        "source_provenance": source_provenance,
        "artifacts": dict(sorted((artifacts or {}).items())),
    }
    validate_portable_manifest(document)
    return document


def portable_manifest_from_publication(document, manifest_sha256=None):
    portable = validate_publication(document)
    provenance = {
        "source_host": portable["source_host"],
        "source_root": portable["source_root"],
        "legacy_location_bound_tree_sha256":
            portable["legacy_location_bound_tree_sha256"],
        "source_location_excluded_from_tree_identity": True,
    }
    if manifest_sha256 is not None:
        if not _lower_hex(manifest_sha256):
            raise RuntimeTreeError("source manifest SHA-256 invalid")
        provenance["source_manifest_sha256"] = manifest_sha256
    return portable_manifest(
        portable["identity"]["entries"], provenance, artifacts={}
    )


def validate_portable_manifest(document):
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "algorithm",
        "tree_content_sha256",
        "content_identity",
        "source_provenance",
        "artifacts",
    }:
        raise RuntimeTreeError("portable tree manifest fields differ")
    if document.get("schema_version") != PORTABLE_MANIFEST_SCHEMA:
        raise RuntimeTreeError("portable tree manifest schema differs")
    portable = content_identity(
        (document.get("content_identity") or {}).get("entries")
    )
    if (
        portable["identity"] != document.get("content_identity")
        or document.get("algorithm") != ALGORITHM
        or document.get("tree_content_sha256")
        != portable["tree_content_sha256"]
    ):
        raise RuntimeTreeError("portable tree manifest identity differs")
    if not isinstance(document.get("source_provenance"), dict):
        raise RuntimeTreeError("portable tree source provenance absent")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, dict):
        raise RuntimeTreeError("portable tree artifacts must be an object")
    by_path = {
        row["path"]: row for row in portable["identity"]["entries"]
    }
    for relative, digest in artifacts.items():
        row = by_path.get(relative)
        if (
            not _lower_hex(digest)
            or not isinstance(row, dict)
            or row.get("type") != "file"
            or row.get("sha256") != digest
        ):
            raise RuntimeTreeError(
                "portable tree artifact binding differs: %s" % relative
            )
    return portable


def inventory_tree(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeTreeError(
            "runtime root missing, symlinked, or not a directory"
        )
    rows = []
    for base_text, dirs, files in os.walk(
        str(root), topdown=True, followlinks=False
    ):
        dirs.sort()
        files.sort()
        base = Path(base_text)
        for name in list(dirs):
            path = base / name
            before = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(before.st_mode):
                target = os.readlink(str(path))
                after = path.lstat()
                if (
                    before.st_dev != after.st_dev
                    or before.st_ino != after.st_ino
                    or before.st_mode != after.st_mode
                    or before.st_mtime_ns != after.st_mtime_ns
                    or target != os.readlink(str(path))
                ):
                    raise RuntimeTreeError(
                        "runtime symlink changed during readback: %s"
                        % relative
                    )
                rows.append(
                    {
                        "path": relative,
                        "type": "symlink",
                        "target": target,
                    }
                )
                dirs.remove(name)
            elif stat.S_ISDIR(before.st_mode):
                rows.append(
                    {
                        "path": relative,
                        "type": "directory",
                        "mode": stat.S_IMODE(before.st_mode),
                    }
                )
            else:
                raise RuntimeTreeError(
                    "special directory entry: %s" % relative
                )
        for name in files:
            path = base / name
            before = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(before.st_mode):
                target = os.readlink(str(path))
                after = path.lstat()
                if (
                    before.st_dev != after.st_dev
                    or before.st_ino != after.st_ino
                    or before.st_mode != after.st_mode
                    or before.st_mtime_ns != after.st_mtime_ns
                    or target != os.readlink(str(path))
                ):
                    raise RuntimeTreeError(
                        "runtime symlink changed during readback: %s"
                        % relative
                    )
                rows.append(
                    {
                        "path": relative,
                        "type": "symlink",
                        "target": target,
                    }
                )
                continue
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeTreeError("special file entry: %s" % relative)
            digest = sha256_file(path)
            after = path.lstat()
            if any(
                getattr(before, field) != getattr(after, field)
                for field in (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_size",
                    "st_mtime_ns",
                )
            ):
                raise RuntimeTreeError(
                    "runtime file changed during readback: %s" % relative
                )
            rows.append(
                {
                    "path": relative,
                    "type": "file",
                    "size": after.st_size,
                    "mode": stat.S_IMODE(after.st_mode),
                    "sha256": digest,
                }
            )
    return normalize_entries(rows)


def verify_tree_manifest(root, manifest):
    authority = validate_portable_manifest(manifest)
    live = content_identity(inventory_tree(root))
    if live["identity"] != authority["identity"]:
        expected = {
            row["path"]: row for row in authority["identity"]["entries"]
        }
        actual = {row["path"]: row for row in live["identity"]["entries"]}
        missing = sorted(set(expected) - set(actual))
        additional = sorted(set(actual) - set(expected))
        changed = sorted(
            path
            for path in set(expected) & set(actual)
            if expected[path] != actual[path]
        )
        raise RuntimeTreeError(
            "live runtime tree differs: missing=%r additional=%r changed=%r"
            % (missing[:20], additional[:20], changed[:20])
        )
    return {
        "schema_version": READBACK_SCHEMA,
        "status": "PASS_EXACT_PATH_INDEPENDENT_TREE_READBACK",
        "root": str(Path(root).resolve()),
        "tree_content_sha256": live["tree_content_sha256"],
        "algorithm": live["algorithm"],
        "file_count": live["identity"]["file_count"],
        "symlink_count": live["identity"]["symlink_count"],
        "directory_count": live["identity"]["directory_count"],
        "total_file_bytes": live["identity"]["total_file_bytes"],
        "source_provenance": manifest["source_provenance"],
        "exact_entry_set_verified": True,
        "directory_path_and_mode_verified": True,
        "file_sha256_size_mode_verified": True,
        "symlink_target_verified": True,
        "special_entries_rejected": True,
        "launch_authorized": False,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
    }


def verify_tree(root, publication):
    portable = portable_manifest_from_publication(publication)
    return verify_tree_manifest(root, portable)


def _load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RuntimeTreeError("authority manifest absent or symlinked")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeTreeError("authority manifest unreadable: %s" % exc)
    if not isinstance(document, dict):
        raise RuntimeTreeError("authority manifest must be an object")
    return document


def _atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % path.name, dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--authority-manifest", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    authority_path = Path(args.authority_manifest)
    authority = _load(authority_path)
    schema = authority.get("schema_version")
    if schema == PUBLICATION_SCHEMA:
        result = verify_tree(args.runtime_root, authority)
    elif schema == PORTABLE_MANIFEST_SCHEMA:
        result = verify_tree_manifest(args.runtime_root, authority)
    else:
        raise RuntimeTreeError(
            "authority manifest schema is unsupported: %r" % schema
        )
    result["authority_manifest_path"] = str(authority_path.resolve())
    result["authority_manifest_sha256"] = sha256_file(authority_path)
    payload = canonical(result)
    if args.output:
        output = Path(args.output)
        _atomic_write(output, payload)
        _atomic_write(
            output.with_name(output.name + ".sha256"),
            ("%s  %s\n" % (sha_bytes(payload), output.name)).encode("ascii"),
        )
    print(payload.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
