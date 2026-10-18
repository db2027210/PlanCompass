#!/usr/bin/env python3
"""Build a fail-closed 181-sample native-metric completion successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "86232f80b4e274aadf1cdbd8acc19033786acfda8dafb6dc18f7bace4bdb61a3"
PARENT_MANIFEST_SHA256 = "2ef63e1ad38dc0217011317c1ed3177c47e6fb46380587482739f87eee761cf7"
PARENT = ROOT / "artifacts/mysql_tpch_inventory_retry_support_v1" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_native_metric_completion_successor_v1"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_native_metric_completion_successor_v1.py"
TEST = "tests/test_mysql_tpch_native_metric_completion_successor_v1.py"
AUDIT = "authority/mysql_tpch_retry4_attempt2_native_marker_root_cause_v1.json"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(str(path) + " is not an object")
    return value


def file_map(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            result[relative] = sha_file(path)
    return result


def verify_parent() -> dict[str, Any]:
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = load(manifest_path)
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or file_map(PARENT) != manifest.get("files")
    ):
        raise BuildError("86232 parent identity/scope/closure differs")
    return manifest


COMPLETION_HELPERS = r'''
NATIVE_METRIC_COMPLETION_SCHEMA = (
    "mysql_algorithm_c_native_metric_producer_completion_v1"
)


def _native_metric_completion_path(metric_path):
    return Path(str(metric_path) + ".producer-complete-v1.json")


def seal_native_metric_producer_completion(
    metric_path, attempt_id, sample_count, last_sequence, last_native_epoch_ms
):
    """Seal an exact finite producer stream; never stand in for a sample."""
    metric_path = Path(metric_path)
    completion_path = _native_metric_completion_path(metric_path)
    sidecar_path = Path(str(completion_path) + ".sha256")
    if (
        metric_path.is_symlink()
        or not metric_path.is_file()
        or completion_path.exists()
        or completion_path.is_symlink()
        or sidecar_path.exists()
        or sidecar_path.is_symlink()
    ):
        raise QualificationError("native metric completion output is not fresh")
    rows = metric_path.read_text(
        encoding="utf-8", errors="strict"
    ).splitlines()
    values = [json.loads(row) for row in rows]
    expected_sequences = list(range(int(sample_count)))
    if (
        len(values) != int(sample_count)
        or [row.get("sequence") for row in values] != expected_sequences
        or any(
            row.get("schema_version")
            != "mysql_algorithm_c_debug_workload_metric_sample_v1"
            or row.get("qualification_attempt_id") != attempt_id
            for row in values
        )
        or int(last_sequence) != int(sample_count) - 1
        or values[-1].get("native_marker_epoch_ms")
        != int(last_native_epoch_ms)
    ):
        raise QualificationError("native metric completion stream closure differs")
    document = {
        "schema_version": NATIVE_METRIC_COMPLETION_SCHEMA,
        "status": "PASS_EXACT_181_SAMPLE_STREAM_SEALED",
        "qualification_attempt_id": attempt_id,
        "sample_count": int(sample_count),
        "first_sequence": 0,
        "last_sequence": int(last_sequence),
        "last_native_epoch_ms": int(last_native_epoch_ms),
        "metric_stream_sha256": sha256(metric_path),
        "missing_sample_count": 0,
        "timeout_count": 0,
        "freshness_threshold_relaxed": False,
        "timeout_threshold_relaxed": False,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    payload = canonical(document)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(completion_path), flags, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    sidecar_payload = (
        sha_bytes(payload) + "  " + completion_path.name + "\n"
    ).encode("ascii")
    descriptor = os.open(str(sidecar_path), flags, 0o600)
    try:
        os.write(descriptor, sidecar_payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return document


def validate_native_metric_producer_completion(
    metric_path, attempt_id, expected_sample_count=MINIMUM_SAMPLES
):
    metric_path = Path(metric_path)
    completion_path = _native_metric_completion_path(metric_path)
    if not valid_sidecar(completion_path):
        raise QualificationError("native metric completion sidecar differs")
    document = load_object(completion_path, "native metric completion")
    if (
        document.get("schema_version") != NATIVE_METRIC_COMPLETION_SCHEMA
        or document.get("status")
        != "PASS_EXACT_181_SAMPLE_STREAM_SEALED"
        or document.get("qualification_attempt_id") != attempt_id
        or document.get("sample_count") != int(expected_sample_count)
        or document.get("first_sequence") != 0
        or document.get("last_sequence") != int(expected_sample_count) - 1
        or document.get("metric_stream_sha256") != sha256(metric_path)
        or document.get("missing_sample_count") != 0
        or document.get("timeout_count") != 0
        or document.get("freshness_threshold_relaxed") is not False
        or document.get("timeout_threshold_relaxed") is not False
        or document.get("capture_authorized") is not False
        or document.get("formal_r0_authorized") is not False
        or document.get("formal_authorized") is not False
        or document.get("accepted_round_authorized") is not False
    ):
        raise QualificationError("native metric completion authority differs")
    return document
'''


def patch_core(payload: bytes) -> bytes:
    text = payload.decode("utf-8")
    helper_anchor = "\ndef run_qualification(\n"
    if text.count(helper_anchor) != 1:
        raise BuildError("run_qualification anchor differs")
    text = text.replace(helper_anchor, "\n" + COMPLETION_HELPERS + helper_anchor)

    state_old = '''    native_metric_producer_state = {"error": None}\n'''
    state_new = '''    native_metric_producer_state = {"error": None, "completion": None}\n'''
    if text.count(state_old) != 1:
        raise BuildError("producer state anchor differs")
    text = text.replace(state_old, state_new)

    loop_old = '''            def produce_native_metrics():
                sequence = 1
                previous_epoch_ms = native_previous_epoch_ms
                while not native_metric_producer_stop.is_set():
                    try:
                        previous_epoch_ms = metric_adapter.emit_fresh_sample(
                            native_adapter_kind,
                            stdout_path,
                            metric_path,
                            spec["qualification_attempt_id"],
                            sequence,
                            previous_epoch_ms,
                            timeout_seconds=2.0,
                        )
                        sequence += 1
                    except metric_adapter.WorkloadMetricAdapterError as exc:
                        if not native_metric_producer_stop.is_set():
                            native_metric_producer_state["error"] = str(exc)
                        return
'''
    loop_new = '''            def produce_native_metrics():
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
                            native_adapter_kind,
                            stdout_path,
                            metric_path,
                            spec["qualification_attempt_id"],
                            sequence,
                            previous_epoch_ms,
                            timeout_seconds=2.0,
                        )
                        sequence += 1
                    except metric_adapter.WorkloadMetricAdapterError as exc:
                        if not native_metric_producer_stop.is_set():
                            native_metric_producer_state["error"] = str(exc)
                        return
                if (not native_metric_producer_stop.is_set()
                        and sequence == MINIMUM_SAMPLES):
                    try:
                        native_metric_producer_state["completion"] = (
                            seal_native_metric_producer_completion(
                                metric_path,
                                spec["qualification_attempt_id"],
                                MINIMUM_SAMPLES,
                                MINIMUM_SAMPLES - 1,
                                previous_epoch_ms,
                            )
                        )
                    except (OSError, UnicodeError, ValueError,
                            QualificationError) as exc:
                        native_metric_producer_state["error"] = str(exc)
'''
    if text.count(loop_old) != 1:
        raise BuildError("native producer loop anchor differs")
    text = text.replace(loop_old, loop_new)

    after_loop_old = '''                samples.append(dynamic_sample)
            if peer_metric_binding is not None:
'''
    after_loop_new = '''                samples.append(dynamic_sample)
            native_metric_producer.join(timeout=3.0)
            if native_metric_producer.is_alive():
                raise QualificationError(
                    "native metric producer completion seal timed out"
                )
            if native_metric_producer_state["error"] is not None:
                raise QualificationError(
                    "native workload metric producer failed before completion seal: %s"
                    % native_metric_producer_state["error"]
                )
            native_metric_completion = (
                validate_native_metric_producer_completion(
                    metric_path,
                    spec["qualification_attempt_id"],
                    MINIMUM_SAMPLES,
                )
            )
            if native_metric_producer_state["completion"] != native_metric_completion:
                raise QualificationError(
                    "native metric in-memory/sealed completion differs"
                )
            if peer_metric_binding is not None:
'''
    if text.count(after_loop_old) != 1:
        raise BuildError("post-sample completion anchor differs")
    text = text.replace(after_loop_old, after_loop_new)
    return text.encode("utf-8")


TEST_PAYLOAD = r'''from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

BUNDLE=Path(__file__).resolve().parents[1]
CORE=BUNDLE/"qualification/mysql_distributed_live_readback_qualification_v1.py"
S=importlib.util.spec_from_file_location("completion_core",CORE)
M=importlib.util.module_from_spec(S);S.loader.exec_module(M)

def row(attempt,sequence):
 return {"schema_version":"mysql_algorithm_c_debug_workload_metric_sample_v1","qualification_attempt_id":attempt,"sequence":sequence,"epoch_seconds":1000.0+sequence,"completed_total":sequence,"query_duration_ms":1.0,"native_adapter_kind":"sql_query_native","native_marker_epoch_ms":1000000+sequence*1000}
def stream(tmp_path,count=181):
 attempt="retry5-"+"a"*40;p=tmp_path/"metric.jsonl";p.write_text("".join(json.dumps(row(attempt,i),sort_keys=True)+"\n" for i in range(count)));return p,attempt
def test_exact_181_stream_seals_and_validates(tmp_path):
 p,a=stream(tmp_path);v=M.seal_native_metric_producer_completion(p,a,181,180,1180000);assert v["missing_sample_count"]==0 and v["timeout_count"]==0 and M.validate_native_metric_producer_completion(p,a)==v
def test_180_stream_never_gets_completion_credit(tmp_path):
 p,a=stream(tmp_path,180)
 with pytest.raises(M.QualificationError,match="stream closure differs"):M.seal_native_metric_producer_completion(p,a,181,180,1180000)
def test_completion_cannot_mask_metric_mutation(tmp_path):
 p,a=stream(tmp_path);M.seal_native_metric_producer_completion(p,a,181,180,1180000);p.write_text(p.read_text()+json.dumps(row(a,181))+"\n")
 with pytest.raises(M.QualificationError,match="authority differs"):M.validate_native_metric_producer_completion(p,a)
def test_no_threshold_relaxation_and_no_sequence_181_request():
 source=CORE.read_text();assert "sequence < MINIMUM_SAMPLES" in source and "sequence <= MINIMUM_SAMPLES" not in source and '"freshness_threshold_relaxed": False' in source and '"timeout_threshold_relaxed": False' in source and "timeout_seconds=2.0" in source
def test_completion_is_sidecar_sealed_and_zero_credit(tmp_path):
 p,a=stream(tmp_path);v=M.seal_native_metric_producer_completion(p,a,181,180,1180000);c=Path(str(p)+".producer-complete-v1.json");s=Path(str(c)+".sha256");assert s.read_text()==hashlib.sha256(c.read_bytes()).hexdigest()+"  "+c.name+"\n" and v["accepted_round_authorized"] is False
'''


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    parent = verify_parent()
    content = {
        relative: (PARENT / relative).read_bytes()
        for relative in sorted(parent["files"])
    }
    content[CORE] = patch_core(content[CORE])
    content[BUILDER] = HERE.read_bytes()
    content[TEST] = TEST_PAYLOAD.encode("utf-8")
    content[AUDIT] = canonical({
        "schema_version": "mysql_tpch_retry4_attempt2_native_marker_root_cause_v1",
        "status": "ROOT_CAUSE_CONFIRMED_LOCAL_CODE_AUDIT_ZERO_CREDIT",
        "observed_failure": "native workload marker is stale",
        "observed_native_metric_rows": 181,
        "last_requested_successful_sequence": 180,
        "erroneous_next_request_sequence": 181,
        "cause": "unbounded producer loop interpreted finite native stream completion as stale heartbeat",
        "remediation": "bound producer to sequences 0..180 and require an exclusive sidecar-sealed completion document bound to the exact metric stream SHA-256",
        "missing_sample_threshold_relaxed": False,
        "timeout_threshold_relaxed": False,
        "freshness_threshold_relaxed": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    })
    sealed_files = {
        relative: sha_bytes(payload) for relative, payload in sorted(content.items())
    }
    identity = {
        key: value for key, value in parent.items()
        if key not in ("bundle_id", "files", "schema_version", "status")
    }
    identity.update({
        "schema_version": "mysql_tpch_native_metric_completion_support_bundle_v1",
        "status": "SEALED_LOCAL_FAIL_CLOSED_COMPLETION_SUPPORT_NOT_EXECUTED",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "builder_sha256": sha_file(HERE),
        "native_metric_expected_sample_count": 181,
        "native_metric_expected_first_sequence": 0,
        "native_metric_expected_last_sequence": 180,
        "native_metric_sequence_181_requested": False,
        "native_metric_completion_sidecar_required": True,
        "native_metric_completion_stream_sha256_required": True,
        "missing_sample_threshold_relaxed": False,
        "timeout_threshold_relaxed": False,
        "freshness_threshold_relaxed": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": sealed_files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".native-completion-", dir=OUT))
        try:
            for relative, payload in sorted(content.items()):
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            freeze(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    if file_map(destination) != sealed_files:
        raise BuildError("published successor closure differs")
    actual = load(destination / "bundle_manifest.json")
    unsigned = dict(actual)
    actual_id = unsigned.pop("bundle_id", None)
    if (
        actual_id != destination.name
        or sha_bytes(canonical(unsigned)) != destination.name
        or sha_file(destination / "bundle_manifest.json")
        != (destination / "bundle_manifest.json.sha256").read_text().split()[0]
    ):
        raise BuildError("published successor content address differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_NATIVE_METRIC_COMPLETION_SUCCESSOR_V1",
        "bundle": str(destination),
        "bundle_id": destination.name,
        "manifest_sha256": sha_file(destination / "bundle_manifest.json"),
        "core_sha256": sha_file(destination / CORE),
        "remote_actions": 0,
        "formal_launch_authorized": False,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, UnicodeError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
