#!/usr/bin/env python3
"""Create and reconcile read-only XP docker-inspect identity snapshots.

Snapshot mode consumes already captured ``docker inspect`` JSON files and
writes a no-overwrite evidence artifact. Reconcile mode compares two such
artifacts. Static Config/HostConfig/Mounts and the necessary network
mode/published-port declaration must be identical. Dynamic NetworkSettings is
always retained and diffed as an observation, but never enters static identity.
"""
import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from mysql_xp_node02_adopted_service_v1 import (
    ServiceError, _dynamic_network_observation, _static_inspect_identity,
    atomic_json,
)


HOST_ID = "node02"


class ReconciliationError(ServiceError):
    pass


def _load(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReconciliationError("%s must be a regular file" % label)
    value = json.loads(path.read_text(encoding="utf-8"))
    return value


def _inspect_row(path, label):
    value = _load(path, label)
    if isinstance(value, list):
        if len(value) != 1 or not isinstance(value[0], dict):
            raise ReconciliationError("%s inspect list differs" % label)
        value = value[0]
    if not isinstance(value, dict):
        raise ReconciliationError("%s inspect object differs" % label)
    for key in ("Id", "Config", "HostConfig", "Mounts", "NetworkSettings"):
        if key not in value:
            raise ReconciliationError("%s missing inspect field %s" % (
                label, key,
            ))
    return value


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_snapshot(database_path, client_path):
    roles = {}
    for role, path in (
        ("database", database_path), ("client", client_path),
    ):
        row = _inspect_row(path, role)
        state = row.get("State") or {}
        pid = int(state.get("Pid") or 0)
        if state.get("Running") is not True or pid <= 1:
            raise ReconciliationError("%s has no real live PID" % role)
        roles[role] = {
            "container_id": row.get("Id"),
            "container_name": row.get("Name"),
            "image_id": row.get("Image"),
            "created": row.get("Created"),
            "pid": pid,
            "static_inspect_identity": _static_inspect_identity(row),
            "dynamic_network_settings_observation":
                _dynamic_network_observation(row),
        }
    return {
        "schema_version": "mysql_xp_adoption_read_only_snapshot_v1",
        "status": "PASS_READ_ONLY_SNAPSHOT",
        "host_id": HOST_ID,
        "scenario": "hybench_xp_io",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "read_only_inspect_inputs": {
            "database": {
                "path": str(Path(database_path).resolve()),
                "sha256": _sha256(database_path),
            },
            "client": {
                "path": str(Path(client_path).resolve()),
                "sha256": _sha256(client_path),
            },
        },
        "roles": roles,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def _field_diff(before, after, prefix=""):
    if type(before) is not type(after):
        return [{
            "path": prefix or "$", "before": before, "after": after,
            "reason": "type_changed",
        }]
    if isinstance(before, dict):
        differences = []
        for key in sorted(set(before) | set(after)):
            path = prefix + "." + key if prefix else key
            if key not in before:
                differences.append({
                    "path": path, "before": None, "after": after[key],
                    "reason": "field_added",
                })
            elif key not in after:
                differences.append({
                    "path": path, "before": before[key], "after": None,
                    "reason": "field_removed",
                })
            else:
                differences.extend(_field_diff(
                    before[key], after[key], path
                ))
        return differences
    if isinstance(before, list):
        if before == after:
            return []
        return [{
            "path": prefix or "$", "before": before, "after": after,
            "reason": "list_changed",
        }]
    if before != after:
        return [{
            "path": prefix or "$", "before": before, "after": after,
            "reason": "value_changed",
        }]
    return []


def reconcile(before_path, after_path):
    before = _load(before_path, "before snapshot")
    after = _load(after_path, "after snapshot")
    for label, value in (("before", before), ("after", after)):
        if (
            not isinstance(value, dict)
            or value.get("schema_version")
            != "mysql_xp_adoption_read_only_snapshot_v1"
            or value.get("host_id") != HOST_ID
            or value.get("formal_scope") != "mysql_only"
            or value.get("cross_engine_comparison_prohibited") is not True
            or set(value.get("roles") or {}) != {"database", "client"}
        ):
            raise ReconciliationError("%s snapshot authority differs" % label)
    static_diffs = []
    dynamic_diffs = []
    for role in ("database", "client"):
        old = before["roles"][role]
        new = after["roles"][role]
        if old.get("container_id") != new.get("container_id"):
            static_diffs.append({
                "path": role + ".container_id",
                "before": old.get("container_id"),
                "after": new.get("container_id"),
                "reason": "same_live_container_changed",
            })
        old_static = old.get("static_inspect_identity") or {}
        new_static = new.get("static_inspect_identity") or {}
        static_diffs.extend(_field_diff(
            old_static.get("payload"), new_static.get("payload"),
            role + ".static_inspect_identity.payload",
        ))
        old_dynamic = (
            old.get("dynamic_network_settings_observation") or {}
        ).get("value")
        new_dynamic = (
            new.get("dynamic_network_settings_observation") or {}
        ).get("value")
        dynamic_diffs.extend(_field_diff(
            old_dynamic, new_dynamic,
            role + ".dynamic_network_settings_observation.value",
        ))
    passed = not static_diffs
    return {
        "schema_version": "mysql_xp_adoption_reconciliation_v1",
        "status": (
            "PASS_SAME_STATIC_IDENTITY"
            if passed else "FAIL_CLOSED_STATIC_IDENTITY_DIFF"
        ),
        "host_id": HOST_ID,
        "scenario": "hybench_xp_io",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "before_snapshot": str(Path(before_path).resolve()),
        "after_snapshot": str(Path(after_path).resolve()),
        "static_identity_equal": passed,
        "fail_closed_static_field_diff": static_diffs,
        "dynamic_network_settings_equal": not dynamic_diffs,
        "dynamic_network_settings_observed_diff": dynamic_diffs,
        "dynamic_network_settings_policy":
            "record_and_diff_but_exclude_from_static_identity",
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot", action="store_true")
    mode.add_argument("--reconcile", action="store_true")
    parser.add_argument("--database-inspect", type=Path)
    parser.add_argument("--client-inspect", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.snapshot:
        if args.database_inspect is None or args.client_inspect is None:
            raise ReconciliationError(
                "snapshot requires both inspect inputs"
            )
        result = make_snapshot(
            args.database_inspect, args.client_inspect
        )
    else:
        if args.before is None or args.after is None:
            raise ReconciliationError(
                "reconcile requires before and after snapshots"
            )
        result = reconcile(args.before, args.after)
    atomic_json(args.output, result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("static_identity_equal", True) else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ReconciliationError) as exc:
        raise SystemExit("FAIL_CLOSED: %s" % exc)
