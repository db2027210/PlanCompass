#!/usr/bin/env python3
"""Build the fail-closed 1 Hz native metric pacing successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6"
PARENT_MANIFEST_SHA256 = "0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4"
PARENT_CORE_SHA256 = "ae8f22b8c4dab4f4e02e023122c72f21b7ebb244ba3d57e136b91468d7542863"
PARENT = ROOT / "artifacts/mysql_tpch_native_metric_completion_successor_v1" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_native_metric_pacing_successor_v1"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode()


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_map(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            result[relative] = sha_file(path)
    return result


PACING_HELPER = '''
NATIVE_METRIC_EMISSION_LEAD_SECONDS = 0.25


def _native_metric_emission_target(window_started_monotonic, sequence):
    """Return the fixed 1 Hz deadline, slightly ahead of its collector tick."""
    if (isinstance(sequence, bool) or not isinstance(sequence, int)
            or sequence < 1 or sequence >= MINIMUM_SAMPLES):
        raise QualificationError("native metric paced sequence differs")
    return (float(window_started_monotonic) + float(sequence)
            - NATIVE_METRIC_EMISSION_LEAD_SECONDS)

'''


def patch_core(payload):
    text = payload.decode()
    helper_anchor = "\ndef run_qualification(\n"
    if text.count(helper_anchor) != 1:
        raise BuildError("run_qualification anchor differs")
    text = text.replace(helper_anchor, "\n" + PACING_HELPER + helper_anchor)
    old_loop = '''            def produce_native_metrics():
                sequence = 1
                previous_epoch_ms = native_previous_epoch_ms
                # Sequence zero was emitted synchronously above.  Produce the
                # exact finite suffix 1..180, then seal completion.  Requesting
                # sequence 181 incorrectly treated normal producer completion
                # as a stale heartbeat.
                while (sequence < MINIMUM_SAMPLES
                       and not native_metric_producer_stop.is_set()):
                    try:
                        previous_epoch_ms = metric_adapter.emit_fresh_sample(
'''
    new_loop = '''            # Bind both emission and collection to one monotonic epoch.
            # Sequence zero was emitted synchronously above.  The remaining
            # exact suffix is emitted at 1 Hz with a 250 ms producer lead; this
            # prevents a fast native query stream from exhausting seq180 before
            # the final collector tick without permitting stale repeats.
            started = time.monotonic()
            def produce_native_metrics():
                sequence = 1
                previous_epoch_ms = native_previous_epoch_ms
                while (sequence < MINIMUM_SAMPLES
                       and not native_metric_producer_stop.is_set()):
                    target = _native_metric_emission_target(started, sequence)
                    delay = target - time.monotonic()
                    if (delay > 0
                            and native_metric_producer_stop.wait(delay)):
                        return
                    try:
                        previous_epoch_ms = metric_adapter.emit_fresh_sample(
'''
    if text.count(old_loop) != 1:
        raise BuildError("unpaced producer loop differs")
    text = text.replace(old_loop, new_loop)
    old_start = '''            native_metric_producer.daemon = True
            native_metric_producer.start()
            started = time.monotonic()
            for index in range(MINIMUM_SAMPLES):
'''
    new_start = '''            native_metric_producer.daemon = True
            native_metric_producer.start()
            for index in range(MINIMUM_SAMPLES):
'''
    if text.count(old_start) != 1:
        raise BuildError("collector epoch anchor differs")
    text = text.replace(old_start, new_start)
    return text.encode()


TEST = r'''from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest

BUNDLE=Path(__file__).resolve().parents[1]
CORE=BUNDLE/"qualification/mysql_distributed_live_readback_qualification_v1.py"
S=importlib.util.spec_from_file_location("paced_core",CORE)
M=importlib.util.module_from_spec(S);S.loader.exec_module(M)

def test_exact_one_hz_schedule_with_collector_lead():
 targets=[M._native_metric_emission_target(1000.0,i) for i in range(1,181)]
 assert targets[0]==1000.75 and targets[-1]==1179.75
 assert all(b-a==1.0 for a,b in zip(targets,targets[1:]))

def test_schedule_rejects_out_of_contract_sequences():
 for value in (True,0,181,-1,1.0):
  with pytest.raises(M.QualificationError,match="paced sequence differs"):
   M._native_metric_emission_target(1000.0,value)

def test_no_repeat_or_threshold_relaxation_added():
 source=CORE.read_text()
 assert "native_metric_producer_stop.wait(delay)" in source
 assert "allow_fresh_repeat=True" in source  # paired peer only, pre-existing
 own_call=source[source.index("dynamic_sample = collect_dynamic_sample("):source.index("previous_sequence = dynamic_sample")]
 assert "allow_fresh_repeat" not in own_call
 assert "timeout_seconds=2.0" in source
 assert '"freshness_threshold_relaxed": False' in source
 assert '"timeout_threshold_relaxed": False' in source

def test_exact_181_completion_contract_remains():
 source=CORE.read_text()
 assert "sequence < MINIMUM_SAMPLES" in source
 assert "sequence <= MINIMUM_SAMPLES" not in source
 assert '"PASS_EXACT_181_SAMPLE_STREAM_SEALED"' in source
 assert "MINIMUM_SAMPLES - 1" in source
'''


ROOT_CAUSE = {
    "schema_version": "mysql_tpch_retry5_index168_native_metric_root_cause_v1",
    "failed_attempt": "ra-mysql-tpch-qual-retry5-20260803t1300.service",
    "failed_attempt_credit": 0,
    "observed_last_successful_sample_index": 168,
    "observed_error": "workload metric contiguous suffix is absent",
    "root_cause": "native producer emitted on marker availability without a shared-window pace and exhausted sequence 180 before collector index 180",
    "fix": "bind producer and collector to one monotonic epoch; emit sequences 1..180 at fixed 1 Hz with 0.25 second producer lead",
    "minimum_sample_count": 181,
    "first_sequence": 0,
    "last_sequence": 180,
    "missing_samples_allowed": 0,
    "stale_repeat_allowed_for_own_stream": False,
    "producer_timeout_seconds": 2.0,
    "freshness_threshold_relaxed": False,
    "timeout_threshold_relaxed": False,
    "formal_scope": "mysql_only",
    "cross_engine_comparison_prohibited": True,
    "postgresql_mutation_prohibited": True,
    "formal_launch_authorized": False,
    "accepted_round_authorized": False,
    "remote_actions": 0,
}


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID
            or sha_file(manifest_path) != PARENT_MANIFEST_SHA256
            or sha_bytes(canonical(unsigned)) != PARENT_ID
            or sha_file(PARENT / CORE) != PARENT_CORE_SHA256
            or file_map(PARENT) != manifest.get("files")
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or manifest.get("postgresql_mutation_prohibited") is not True
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise BuildError("da455 parent closure differs")


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build():
    verify_parent()
    shared_test = (PARENT / "tests/test_mysql_tpch_tpcc_shared_collector_current_v1.py").read_text()
    shared_test = shared_test.replace(
        'ROOT = Path(__file__).resolve().parents[1]\nENTRY = ROOT / "shell/mysql_tpch_tpcc_shared_collector_current_v1/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"',
        'BUNDLE = Path(__file__).resolve().parents[1]\nROOT = Path(__file__).resolve().parents[4]\nENTRY = BUNDLE / "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"',
    )
    overrides = {
        CORE: patch_core((PARENT / CORE).read_bytes()),
        "tests/test_mysql_tpch_tpcc_shared_collector_current_v1.py": shared_test.encode(),
        "tests/test_mysql_tpch_native_metric_pacing_successor_v1.py": TEST.encode(),
        "authority/mysql_tpch_retry5_index168_native_metric_root_cause_v1.json": canonical(ROOT_CAUSE),
        "authority/build_mysql_tpch_native_metric_pacing_successor_v1.py": HERE.read_bytes(),
    }
    content = {}
    for path in sorted(PARENT.rglob("*")):
        relative = path.relative_to(PARENT).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            content[relative] = path.read_bytes()
    content.update(overrides)
    files = {name: sha_bytes(payload) for name, payload in sorted(content.items())}
    unsigned = {
        "schema_version": "mysql_tpch_native_metric_pacing_successor_bundle_v1",
        "status": "SEALED_LOCAL_FAIL_CLOSED_1HZ_PACING_SUPPORT_NOT_EXECUTED",
        "immutable": True,
        "parent_bundle_id": PARENT_ID,
        "parent_manifest_sha256": PARENT_MANIFEST_SHA256,
        "root_cause": "unpaced producer exhausted seq180 before the 180-second collector window ended",
        "producer_schedule": "shared_monotonic_epoch_fixed_1hz_with_0.25s_lead",
        "minimum_sample_count": 181,
        "first_sequence": 0,
        "last_sequence": 180,
        "missing_samples_allowed": 0,
        "stale_repeat_allowed_for_own_stream": False,
        "freshness_threshold_relaxed": False,
        "timeout_threshold_relaxed": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
        "files": files,
    }
    bundle_id = sha_bytes(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    destination = OUT / bundle_id
    if destination.exists():
        if sha_file(destination / "bundle_manifest.json") != sha_bytes(canonical(manifest)):
            raise BuildError("existing bundle differs")
        return destination
    OUT.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".pacing-", dir=OUT))
    try:
        for name, payload in content.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        manifest_payload = canonical(manifest)
        (staging / "bundle_manifest.json").write_bytes(manifest_payload)
        (staging / "bundle_manifest.json.sha256").write_text(
            sha_bytes(manifest_payload) + "  bundle_manifest.json\n"
        )
        freeze(staging)
        os.replace(staging, destination)
    finally:
        if staging.exists():
            for path in sorted(staging.rglob("*"), reverse=True):
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
            staging.rmdir()
    return destination


if __name__ == "__main__":
    result = build()
    print(json.dumps({
        "bundle": str(result),
        "bundle_id": result.name,
        "manifest_sha256": sha_file(result / "bundle_manifest.json"),
        "core_sha256": sha_file(result / CORE),
        "remote_actions": 0,
    }, sort_keys=True))
