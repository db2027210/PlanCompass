#!/usr/bin/env python3
"""Read-only comparison of sealed JOB v21 source/stage inventories."""

import hashlib
import json
import pathlib
import sys


root = pathlib.Path(sys.argv[1])
for kind in ("runtime", "template"):
    source_path = root / kind / "source-post.json"
    stage_path = root / kind / "stage.json"
    source = json.loads(source_path.read_text())
    stage = json.loads(stage_path.read_text())
    source_identity = source["identity"]
    stage_identity = stage["identity"]
    source_entries = {row["path"]: row for row in source_identity["entries"]}
    stage_entries = {row["path"]: row for row in stage_identity["entries"]}
    paths = sorted(set(source_entries) | set(stage_entries))
    categories = {key: [] for key in (
        "missing", "type", "content", "hardlink", "owner_mode", "xattr",
        "mtime", "other",
    )}
    for path in paths:
        left = source_entries.get(path)
        right = stage_entries.get(path)
        if left is None or right is None:
            categories["missing"].append(path)
            continue
        if left.get("type") != right.get("type"):
            categories["type"].append(path)
        if any(left.get(key) != right.get(key)
               for key in ("size", "sha256", "target")):
            categories["content"].append(path)
        if left.get("hardlink_to") != right.get("hardlink_to"):
            categories["hardlink"].append(path)
        if any(left.get(key) != right.get(key)
               for key in ("uid", "gid", "mode")):
            categories["owner_mode"].append(path)
        if left.get("xattrs_base64") != right.get("xattrs_base64"):
            categories["xattr"].append(path)
        if left.get("mtime_ns") != right.get("mtime_ns"):
            categories["mtime"].append(path)
    root_diff = {
        key: [source_identity.get(key), stage_identity.get(key)]
        for key in (
            "root_mode", "root_uid", "root_gid", "root_mtime_ns",
            "root_xattrs_base64",
        )
        if source_identity.get(key) != stage_identity.get(key)
    }
    print(json.dumps({
        "kind": kind,
        "source_inventory_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "stage_inventory_sha256": hashlib.sha256(stage_path.read_bytes()).hexdigest(),
        "source_identity": source["content_identity_sha256"],
        "stage_identity": stage["content_identity_sha256"],
        "entry_count": [source["entry_count"], stage["entry_count"]],
        "logical_unique_bytes": [
            source["logical_unique_file_bytes"],
            stage["logical_unique_file_bytes"],
        ],
        "allocated_unique_bytes": [
            source["observed_allocated_bytes_unique_inodes"],
            stage["observed_allocated_bytes_unique_inodes"],
        ],
        "root_diff": root_diff,
        "diff_counts": {key: len(value) for key, value in categories.items()},
        "diff_examples": {
            key: value[:12] for key, value in categories.items() if value
        },
    }, sort_keys=True))
