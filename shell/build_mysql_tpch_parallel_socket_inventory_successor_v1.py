#!/usr/bin/env python3
"""Build fail-closed parallel two-socket Docker inventory on top of ef511."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "ef511522ca7947c8e5bbaebc968829a89a0538d9b925a26a0560b4a0fe61dc88"
PARENT_SHA = "e077588760d0dce397664655666ab3bba6f36b2a5996aee61dd7f79d40149d90"
PARENT_CORE = "cccd6959093bc337e39eee849111fe87c3d8193c5aa19d1a34605a7b6c67833e"
PARENT_ENTRY = "5545dcb6277fd8979a4b6e1994bf7941aa8db19b4a369498bf22ae4b8c2412b1"
PARENT = ROOT / "artifacts/mysql_tpch_native_metric_exact_consumer_successor_v1" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_parallel_socket_inventory_successor_v1"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_map(root):
    return {
        path.relative_to(root).as_posix(): sha_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in ("bundle_manifest.json", "bundle_manifest.json.sha256")
    }


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError(label + " anchor differs")
    return text.replace(old, new)


def patch_core(payload):
    text = payload.decode()
    text = replace_once(
        text,
        "def _docker_container_inventory():\n",
        """def _docker_container_inventory(docker_cli=None, root_inventory=None):
    effective_docker_cli = DOCKER_CLI if docker_cli is None else tuple(docker_cli)
    effective_root_inventory = (
        _root_docker_mount_inventory if root_inventory is None else root_inventory
    )
    if not effective_docker_cli or not callable(effective_root_inventory):
        raise QualificationError("Docker inventory dependency differs")
""",
        "Docker inventory signature",
    )
    start = text.index("def _docker_container_inventory(")
    end = text.index("\ndef rolling_process_inventory(", start)
    block = text[start:end]
    if block.count("[*DOCKER_CLI") != 2:
        raise BuildError("Docker CLI call coverage differs")
    if block.count("_root_docker_mount_inventory()") != 2:
        raise BuildError("root inventory call coverage differs")
    block = block.replace("[*DOCKER_CLI", "[*effective_docker_cli")
    block = block.replace("_root_docker_mount_inventory()", "effective_root_inventory()")
    return (text[:start] + block + text[end:]).encode()


def patch_entry(payload):
    text = payload.decode()
    text = replace_once(
        text,
        "import importlib.util\n",
        "import concurrent.futures\nimport importlib.util\n",
        "concurrent futures import",
    )
    old = '''def shared_docker_container_inventory():
    """Merge exact inventories from both sealed node04 Docker sockets."""
    selected = _active_profile
    if selected is None:
        raise core.QualificationError("shared Docker profile is not bound")
    merged = {}
    evidence_by_unit = {}
    try:
        for profile in PROFILES.values():
            _activate_profile(profile)
            containers, evidence = _original_docker_container_inventory()
            overlap = set(merged).intersection(containers)
            if overlap:
                raise core.QualificationError(
                    "container identity overlaps sealed Docker sockets"
                )
            for container_id, row in containers.items():
                row["docker_socket_uri"] = profile["socket_uri"]
                row["docker_unit"] = profile["unit"]
                merged[container_id] = row
            evidence_by_unit[profile["unit"]] = evidence
    finally:
        _activate_profile(selected)
'''
    new = '''def _docker_inventory_for_profile(profile):
    """Collect one socket without mutating the process-wide active profile."""
    return _original_docker_container_inventory(
        docker_cli=("docker", "--host", profile["socket_uri"]),
        root_inventory=lambda: _root_docker_mount_inventory(profile),
    )


def shared_docker_container_inventory():
    """Merge concurrent full inventories from both sealed Docker sockets."""
    if _active_profile is None:
        raise core.QualificationError("shared Docker profile is not bound")
    profiles = tuple(PROFILES.values())
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(profiles),
        thread_name_prefix="sealed-docker-inventory",
    ) as executor:
        futures = [
            executor.submit(_docker_inventory_for_profile, profile)
            for profile in profiles
        ]
        # Resolve in sealed profile order.  The executor context waits for every
        # submitted full inventory even when one socket fails closed.
        inventories = [future.result() for future in futures]
    merged = {}
    evidence_by_unit = {}
    for profile, inventory in zip(profiles, inventories):
        containers, evidence = inventory
        overlap = set(merged).intersection(containers)
        if overlap:
            raise core.QualificationError(
                "container identity overlaps sealed Docker sockets"
            )
        for container_id, row in containers.items():
            row["docker_socket_uri"] = profile["socket_uri"]
            row["docker_unit"] = profile["unit"]
            merged[container_id] = row
        evidence_by_unit[profile["unit"]] = evidence
'''
    text = replace_once(text, old, new, "shared Docker inventory loop")
    old = '''def _root_docker_mount_inventory():
    if _active_profile is None:
        raise core.QualificationError("shared Docker profile is not bound")
'''
    new = '''def _root_docker_mount_inventory(profile=None):
    selected_profile = _active_profile if profile is None else profile
    if selected_profile not in PROFILES.values():
        raise core.QualificationError("shared Docker profile is not bound")
'''
    text = replace_once(text, old, new, "explicit root inventory profile")
    start = text.index("def _root_docker_mount_inventory(profile=None):")
    end = text.index("\ndef dedicated_daemon_readback(", start)
    block = text[start:end]
    if block.count('_active_profile["socket_uri"]') != 3:
        raise BuildError("root inventory active-profile coverage differs")
    block = block.replace('_active_profile["socket_uri"]',
                          'selected_profile["socket_uri"]')
    return (text[:start] + block + text[end:]).encode()


TEST = r'''from __future__ import annotations
import importlib.util
import threading
from pathlib import Path
import pytest

BUNDLE = Path(__file__).resolve().parents[1]
ENTRY = BUNDLE / "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
CORE = BUNDLE / "qualification/mysql_distributed_live_readback_qualification_v1.py"
spec = importlib.util.spec_from_file_location("parallel_socket_entry", ENTRY)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def _row(container_id, profile):
    return {
        container_id: {
            "container_id": container_id,
            "name": profile["unit"],
            "init_pid": 100,
            "mounts": [],
            "mounts_sha256": "0" * 64,
        }
    }


def test_two_full_socket_inventories_overlap_in_time_and_preserve_order(monkeypatch):
    barrier = threading.Barrier(2)
    calls = []
    lock = threading.Lock()
    profiles = tuple(M.PROFILES.values())

    def fake(profile):
        with lock:
            calls.append(profile["socket_uri"])
        barrier.wait(timeout=1)
        container_id = ("a" if profile is profiles[0] else "b") * 64
        return _row(container_id, profile), {"socket": profile["socket_uri"]}

    monkeypatch.setattr(M, "_docker_inventory_for_profile", fake)
    M._active_profile = profiles[0]
    merged, evidence = M.shared_docker_container_inventory()
    assert sorted(merged) == ["a" * 64, "b" * 64]
    assert calls == [profile["socket_uri"] for profile in profiles]
    assert list(evidence["multi_socket_closure"]["sockets"]) == [
        profile["unit"] for profile in profiles
    ]
    assert M._active_profile is profiles[0]


def test_each_invocation_runs_both_sockets_without_cache(monkeypatch):
    calls = []
    profiles = tuple(M.PROFILES.values())

    def fake(profile):
        calls.append(profile["socket_uri"])
        container_id = ("a" if profile is profiles[0] else "b") * 64
        return _row(container_id, profile), {}

    monkeypatch.setattr(M, "_docker_inventory_for_profile", fake)
    M._active_profile = profiles[0]
    M.shared_docker_container_inventory()
    M.shared_docker_container_inventory()
    assert calls.count(profiles[0]["socket_uri"]) == 2
    assert calls.count(profiles[1]["socket_uri"]) == 2


def test_one_socket_failure_fails_closed_after_both_started(monkeypatch):
    barrier = threading.Barrier(2)
    started = []
    lock = threading.Lock()
    profiles = tuple(M.PROFILES.values())

    def fake(profile):
        with lock:
            started.append(profile["socket_uri"])
        barrier.wait(timeout=1)
        if profile is profiles[1]:
            raise M.core.QualificationError("socket inventory failed")
        return _row("a" * 64, profile), {}

    monkeypatch.setattr(M, "_docker_inventory_for_profile", fake)
    M._active_profile = profiles[0]
    with pytest.raises(M.core.QualificationError, match="socket inventory failed"):
        M.shared_docker_container_inventory()
    assert sorted(started) == sorted(profile["socket_uri"] for profile in profiles)


def test_worker_uses_explicit_socket_and_never_changes_active_profile(monkeypatch):
    profiles = tuple(M.PROFILES.values())
    M._active_profile = profiles[0]
    observed = {}

    def fake_root(profile=None):
        observed["root_profile"] = profile
        return {"containers": []}, {}

    def fake_original(*, docker_cli, root_inventory):
        observed["docker_cli"] = docker_cli
        root_inventory()
        return {}, {}

    monkeypatch.setattr(M, "_root_docker_mount_inventory", fake_root)
    monkeypatch.setattr(M, "_original_docker_container_inventory", fake_original)
    M._docker_inventory_for_profile(profiles[1])
    assert observed["docker_cli"] == (
        "docker", "--host", profiles[1]["socket_uri"]
    )
    assert observed["root_profile"] is profiles[1]
    assert M._active_profile is profiles[0]


def test_formal_thresholds_and_exact_consumer_are_unchanged():
    source = CORE.read_text()
    assert "MAXIMUM_SAMPLE_GAP_SECONDS = 5.0" in source
    assert "MINIMUM_SAMPLES = 181" in source
    assert "timeout_seconds=2.5" in source
    assert "consume_exact_sequence=True" in source
    assert '"freshness_threshold_relaxed": False' in source
    assert '"timeout_threshold_relaxed": False' in source
    assert "rolling_process_inventory(" in source
'''


AUDIT = {
    "schema_version": "mysql_tpch_retry7_parallel_socket_inventory_root_cause_v1",
    "observation": "retry7 failed near sample 87 because two full Docker socket inventories were serialized each dynamic round",
    "root_cause": "approximately 1.12 seconds of serialized per-round inventory exceeded the fixed 1 Hz cadence and accumulated exact-sequence age",
    "fix": "run both complete socket inventories concurrently with explicit per-worker Docker CLI and root-helper profile dependencies",
    "cache_used": False,
    "both_sockets_fully_inventoried_each_round": True,
    "one_socket_failure_is_fatal": True,
    "deterministic_merge_order": "sealed profile order",
    "minimum_sample_count": 181,
    "inherited_maximum_sample_gap_seconds": 5.0,
    "inherited_peer_workload_timeout_seconds": 2.5,
    "freshness_threshold_relaxed": False,
    "timeout_threshold_relaxed": False,
    "rolling_interference_gate_relaxed": False,
    "formal_scope": "mysql_only",
    "cross_engine_comparison_prohibited": True,
    "postgresql_mutation_prohibited": True,
    "formal_launch_authorized": False,
    "accepted_round_authorized": False,
    "accepted_rounds_credit": 0,
    "remote_actions": 0,
}


def verify_parent():
    manifest = json.loads((PARENT / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_file(PARENT / "bundle_manifest.json") != PARENT_SHA
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or sha_file(PARENT / CORE) != PARENT_CORE
        or sha_file(PARENT / ENTRY) != PARENT_ENTRY
        or file_map(PARENT) != (manifest.get("files") or {})
        or manifest.get("formal_launch_authorized") is not False
    ):
        raise BuildError("ef511 parent closure differs")


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build():
    verify_parent()
    content = {}
    for path in sorted(PARENT.rglob("*")):
        relative = path.relative_to(PARENT).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            content[relative] = path.read_bytes()
    content.update({
        CORE: patch_core((PARENT / CORE).read_bytes()),
        ENTRY: patch_entry((PARENT / ENTRY).read_bytes()),
        "tests/test_mysql_tpch_parallel_socket_inventory_successor_v1.py": TEST.encode(),
        "authority/mysql_tpch_retry7_parallel_socket_inventory_root_cause_v1.json": canonical(AUDIT),
        "authority/build_mysql_tpch_parallel_socket_inventory_successor_v1.py": HERE.read_bytes(),
    })
    files = {name: sha_bytes(value) for name, value in sorted(content.items())}
    unsigned = {
        "schema_version": "mysql_tpch_parallel_socket_inventory_successor_bundle_v1",
        "status": "SEALED_LOCAL_FAIL_CLOSED_PARALLEL_INVENTORY_NOT_EXECUTED",
        "immutable": True,
        "parent_bundle_id": PARENT_ID,
        "parent_manifest_sha256": PARENT_SHA,
        "root_cause": AUDIT["root_cause"],
        "inventory_execution": "two_socket_concurrent_full_inventory_every_round",
        "inventory_cache_used": False,
        "one_socket_failure_is_fatal": True,
        "deterministic_merge_order": "sealed profile order",
        "own_consumer_policy": "exact_sequence_0_then_previous_plus_1",
        "minimum_sample_count": 181,
        "first_sequence": 0,
        "last_sequence": 180,
        "missing_samples_allowed": 0,
        "inherited_maximum_sample_gap_seconds": 5.0,
        "inherited_peer_workload_timeout_seconds": 2.5,
        "freshness_threshold_relaxed": False,
        "timeout_threshold_relaxed": False,
        "rolling_interference_gate_relaxed": False,
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
            raise BuildError("existing destination differs")
        return destination
    OUT.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".parallel-inventory-", dir=OUT))
    try:
        for name, value in content.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        raw = canonical(manifest)
        (staging / "bundle_manifest.json").write_bytes(raw)
        (staging / "bundle_manifest.json.sha256").write_text(
            sha_bytes(raw) + "  bundle_manifest.json\n"
        )
        freeze(staging)
        os.replace(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


if __name__ == "__main__":
    bundle = build()
    print(json.dumps({
        "bundle": str(bundle),
        "bundle_id": bundle.name,
        "manifest_sha256": sha_file(bundle / "bundle_manifest.json"),
        "core_sha256": sha_file(bundle / CORE),
        "entry_sha256": sha_file(bundle / ENTRY),
        "remote_actions": 0,
    }, sort_keys=True))
