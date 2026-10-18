#!/usr/bin/env python3
"""Build the v10 shared collector with a fresh paired-window epoch."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "b0f91ca4aa3f8e0c439dbfea7578be383d50c129c4810a978927a0912cc5f9b5"
PARENT_SHA = "07740d4f58f1ece72e6986709ee21b640badc9788604b05a14e71a8b4349527f"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v9" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v10"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v10"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v10.py"
INVALIDATION = "authority/v9_paired_warmup_epoch_invalidation.json"
TEST = "tests/test_shared_collector_paired_warmup_epoch_v10.py"


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
        != "mysql_tpch_tpcc_shared_collector_bundle_v9"
        or sha(canonical(unsigned)) != PARENT_ID
        or digest(path) != PARENT_SHA
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise BuildError("v9 parent identity/scope differs")
    for relative, expected in value["files"].items():
        candidate = PARENT / relative
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or digest(candidate) != expected
        ):
            raise BuildError("v9 parent closure differs: " + relative)
    return value


def patch_core(data):
    source = data.decode("utf-8")
    old = '''            if peer_metric_binding is not None:
                workload_metric_sample(
                    Path(
                        peer_metric_binding[
                            "workload_metric_stream_path"
                        ]
                    ),
                    peer_metric_binding["qualification_attempt_id"],
                    None,
                    timeout_seconds=90.0,
                )
            started = time.monotonic()
'''
    new = '''            if peer_metric_binding is not None:
                workload_metric_sample(
                    Path(
                        peer_metric_binding[
                            "workload_metric_stream_path"
                        ]
                    ),
                    peer_metric_binding["qualification_attempt_id"],
                    None,
                    timeout_seconds=90.0,
                )
                # The first marker is only a startup-barrier probe.  Paired
                # workloads deliberately have asymmetric warmup (TPC-C 60s,
                # TPC-H immediate), so that probe can be old by the time the
                # peer appears.  Start the common window from a newly emitted
                # sequence zero without weakening the 2.5s freshness gate.
                if metric_path.is_symlink() or not metric_path.is_file():
                    raise QualificationError(
                        "paired startup metric stream changed before epoch reset"
                    )
                metric_path.unlink()
                try:
                    native_previous_epoch_ms = (
                        metric_adapter.emit_fresh_sample(
                            native_adapter_kind,
                            stdout_path,
                            metric_path,
                            spec["qualification_attempt_id"],
                            0,
                            native_previous_epoch_ms,
                        )
                    )
                except metric_adapter.WorkloadMetricAdapterError as exc:
                    raise QualificationError(
                        "paired startup metric epoch reset failed: %s" % exc
                    )
            started = time.monotonic()
'''
    if source.count(old) != 1:
        raise BuildError("paired startup barrier patch cardinality differs")
    return source.replace(old, new).encode("utf-8")


def test_source():
    return b'''from __future__ import annotations\nimport hashlib,json,py_compile\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef test_v10_identity_and_fresh_paired_epoch():\n m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")\n raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()\n assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()\n assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v10"\n assert m["paired_window_epoch_reset_after_barrier"] is True\n assert m["maximum_sample_gap_seconds"]==2.5\n assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False\n for relative,expected in m["files"].items():assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==expected\n core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()\n barrier='timeout_seconds=90.0,';reset='metric_path.unlink()';fresh='"paired startup metric epoch reset failed: %s" % exc';started='started = time.monotonic()'\n assert core.index(barrier)<core.index(reset)<core.index(fresh)<core.index(started,core.index(fresh))\n assert 'MAXIMUM_SAMPLE_GAP_SECONDS = 2.5' in core\n assert 'sequence <= previous_sequence' in core and 'workload metric sample age exceeded' in core\n py_compile.compile(str(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-core.pyc")))\n'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in shared collector v10")
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
    payload[CORE] = patch_core(payload[CORE])
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v9",
                SCHEMA.encode("ascii"),
            )
    payload[BUILDER] = Path(__file__).read_bytes()
    modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical(
        {
            "schema_version":
            "mysql_shared_collector_paired_warmup_epoch_invalidation_v1",
            "invalidated_collector_bundle_id": PARENT_ID,
            "failed_attempts": [
                "tpch-a-paired-v9i-20260801t125600p08-6c3809b8",
                "tpcc-b-paired-v9i-20260801t125600p08-7e2e13aa",
            ],
            "observed_terminal_error":
            "workload metric sample missing/stale/timed out: workload metric sample age exceeded",
            "reason":
            "the early side emitted sequence zero before waiting for the peer's asymmetric warmup and then reused that old probe as measurement sample zero",
            "replacement_behavior":
            "after both peer streams are present, replace the own startup probe with a fresh sequence zero and retain the exact 2.5 second freshness gate",
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
            "SEALED_FRESH_PAIRED_WINDOW_EPOCH_NOT_LAUNCH_AUTHORITY",
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_SHA,
            "paired_window_epoch_reset_after_barrier": True,
            "maximum_sample_gap_seconds": 2.5,
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
            raise BuildError("existing shared v10 differs")
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
