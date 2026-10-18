#!/usr/bin/env python3
"""Build the v11 shared collector with bounded zero-race snapshot retries."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "36114b4a38180448c1f3e06d19974c8993f073243acd7cf69b45b582bcf4d532"
PARENT_SHA = "4e075f40f9816d2294a01ef4fbb00a339f721e3a7d86534a3f733630970c0eff"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v10" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v11"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v11"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v11.py"
INVALIDATION = "authority/v10_proc_snapshot_race_invalidation.json"
TEST = "tests/test_shared_collector_zero_race_retry_v11.py"
MAXIMUM_SNAPSHOT_ATTEMPTS = 16


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def verified_parent():
    path = PARENT / "bundle_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(value)
    unsigned.pop("bundle_id", None)
    if (
        value.get("bundle_id") != PARENT_ID
        or value.get("schema_version")
        != "mysql_tpch_tpcc_shared_collector_bundle_v10"
        or sha(canonical(unsigned)) != PARENT_ID
        or digest(path) != PARENT_SHA
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("postgresql_mutation_prohibited") is not True
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise BuildError("v10 parent identity/scope differs")
    for relative, expected in value["files"].items():
        candidate = PARENT / relative
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or digest(candidate) != expected
        ):
            raise BuildError("v10 parent closure differs: " + relative)
    return value


def patch_entry(data):
    source = data.decode("utf-8")
    import_old = "import subprocess\nimport sys\n"
    import_new = "import subprocess\nimport sys\nimport time\n"
    retry_old = '''    snapshot_attempts = 1
    while int(result.get("race_count") or 0) > 0 and snapshot_attempts < 4:
        result = _original_rolling_process_inventory(*args, **kwargs)
        snapshot_attempts += 1
'''
    retry_new = '''    snapshot_attempts = 1
    while (
        int(result.get("race_count") or 0) > 0
        and snapshot_attempts < 16
    ):
        # Short-lived SQL adapter children can exit while /proc is being
        # enumerated.  Retry the complete read-only snapshot with bounded
        # backoff; an accepted sample still requires an exact zero-race view.
        time.sleep(0.01 * min(snapshot_attempts, 4))
        result = _original_rolling_process_inventory(*args, **kwargs)
        snapshot_attempts += 1
'''
    policy_old = '''        "maximum_complete_snapshot_attempts": 4,
        "race_rows_ignored": False,
        "accepted_snapshot_requires_zero_races": True,
'''
    policy_new = '''        "maximum_complete_snapshot_attempts": 16,
        "retry_backoff_milliseconds": [10, 20, 30, 40],
        "race_rows_ignored": False,
        "accepted_snapshot_requires_zero_races": True,
'''
    for old, new, label in (
        (import_old, import_new, "time import"),
        (retry_old, retry_new, "snapshot retry"),
        (policy_old, policy_new, "snapshot policy"),
    ):
        if source.count(old) != 1:
            raise BuildError(label + " patch cardinality differs")
        source = source.replace(old, new)
    return source.encode("utf-8")


def test_source():
    return b'''from __future__ import annotations\nimport hashlib,json,py_compile\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef test_v11_identity_and_zero_race_retry():\n m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")\n raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()\n assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()\n assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v11"\n assert m["proc_snapshot_maximum_attempts"]==16\n assert m["proc_snapshot_acceptance_requires_zero_races"] is True\n assert m["maximum_sample_gap_seconds"]==2.5\n assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False\n for relative,expected in m["files"].items():assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==expected\n entry=(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py").read_text()\n assert "snapshot_attempts < 16" in entry\n assert "time.sleep(0.01 * min(snapshot_attempts, 4))" in entry\n assert '"maximum_complete_snapshot_attempts": 16' in entry\n assert '"race_rows_ignored": False' in entry\n assert '"accepted_snapshot_requires_zero_races": True' in entry\n py_compile.compile(str(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-entry.pyc")))\n'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in shared collector v11")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verified_parent()
    payload = {
        relative: (PARENT / relative).read_bytes()
        for relative in parent["files"]
    }
    modes = {
        relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
        for relative in parent["files"]
    }
    payload[ENTRY] = patch_entry(payload[ENTRY])
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v10",
                SCHEMA.encode("ascii"),
            )
    payload[BUILDER] = Path(__file__).read_bytes()
    modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical(
        {
            "schema_version":
            "mysql_shared_collector_proc_snapshot_race_invalidation_v1",
            "invalidated_collector_bundle_id": PARENT_ID,
            "failed_attempts": [
                "tpch-a-paired-v10e-20260801t134500p08-6c3809b8",
                "tpcc-b-paired-v10e-20260801t134500p08-7e2e13aa",
            ],
            "observed_terminal_error": "process_inventory_race",
            "reason":
            "four immediate snapshots can all intersect short-lived SQL adapter children",
            "replacement_behavior":
            "retry complete read-only snapshots up to 16 times with bounded backoff; require zero races for acceptance",
            "qualification_credit": 0,
            "r0_credit": 0,
            "accepted_round_credit": 0,
            "resource_workload_four_flow_algorithm_c_unchanged": True,
            "postgresql_mutation": False,
        }
    )
    modes[INVALIDATION] = 0o444
    payload[TEST] = test_source()
    modes[TEST] = 0o444
    unsigned = {
        key: value
        for key, value in parent.items()
        if key not in {"bundle_id", "files"}
    }
    unsigned.update(
        {
            "schema_version": SCHEMA,
            "status":
            "SEALED_ZERO_RACE_SNAPSHOT_RETRY_NOT_LAUNCH_AUTHORITY",
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_SHA,
            "proc_snapshot_maximum_attempts": MAXIMUM_SNAPSHOT_ATTEMPTS,
            "proc_snapshot_retry_backoff_milliseconds": [10, 20, 30, 40],
            "proc_snapshot_acceptance_requires_zero_races": True,
            "resource_workload_four_flow_algorithm_c_unchanged": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "files": {
                relative: sha(data)
                for relative, data in sorted(payload.items())
            },
        }
    )
    bundle_id = sha(canonical(unsigned))
    manifest = {**unsigned, "bundle_id": bundle_id}
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    if destination.exists():
        if json.loads(
            (destination / "bundle_manifest.json").read_text(encoding="utf-8")
        ) != manifest:
            raise BuildError("existing shared v11 differs")
        print(destination)
        return
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.chmod(path, modes.get(relative, 0o444) & ~0o222)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(raw) + "  bundle_manifest.json\n", encoding="ascii"
        )
        freeze(stage)
        os.rename(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(destination)


if __name__ == "__main__":
    main()
