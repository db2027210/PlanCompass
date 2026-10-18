from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

from shell import build_mysql_job_tpch_distributed_promotion_v1 as promotion
from shell import verify_mysql_job_tpch_distributed_promotion_evidence_v1 as verifier


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_set(
    root: Path,
    scenario: str,
    now: datetime,
) -> tuple[Path, Path, Path]:
    expected = verifier.EXPECTED[scenario]
    attempt = f"{scenario}-attempt-20260729-abcdef0123456789"
    captured = now - timedelta(seconds=5)
    expires = now + timedelta(minutes=10)

    live_raw = root / "live.raw"
    live_raw.write_text("live readback raw\n", encoding="utf-8")
    runtime = {
        "python": {
            "root": expected["python_root"],
            "interpreter_path": expected["python_interpreter"],
            "tree_sha256": verifier.PYTHON_TREE_SHA256,
            "tree_sha256_semantics":
                "authority_legacy_location_bound_inventory_digest",
            "tree_content_sha256": verifier.PYTHON_TREE_CONTENT_SHA256,
            "tree_content_identity_algorithm":
                "sha256_canonical_relative_tree_entries_v3",
            "authority_manifest_sha256": verifier.PYTHON_MANIFEST_SHA256,
            "exact_entry_set_verified": True,
            "directory_entry_set_verified": True,
            "file_sha256_size_mode_verified": True,
            "symlink_target_verified": True,
            "special_entries_rejected": True,
            "interpreter_sha256": verifier.PYTHON_EXECUTABLE_SHA256,
            "version": "3.11.11",
            "live_tree_and_interpreter_readback": True,
        },
        "jdk": {},
    }
    if expected["jdk_required"]:
        runtime["jdk"] = {
            "root": expected["jdk_root"],
            "java_path": expected["java_path"],
            "tree_sha256": verifier.JDK_TREE_SHA256,
            "tree_sha256_semantics":
                "authority_legacy_location_bound_inventory_digest",
            "tree_content_sha256": verifier.JDK_TREE_CONTENT_SHA256,
            "tree_content_identity_algorithm":
                "sha256_canonical_relative_tree_entries_v3",
            "authority_manifest_sha256": verifier.JDK_MANIFEST_SHA256,
            "exact_entry_set_verified": True,
            "directory_entry_set_verified": True,
            "file_sha256_size_mode_verified": True,
            "symlink_target_verified": True,
            "special_entries_rejected": True,
            "java_sha256": verifier.JAVA_EXECUTABLE_SHA256,
            "live_tree_and_java_readback": True,
        }
    live = {
        "schema_version":
            "mysql_algorithm_c_distributed_live_readback_evidence_v1",
        "status": "PASS",
        "scenario": expected["scenario"],
        "attempt_token": attempt,
        "captured_at_utc": captured.isoformat(),
        "expires_at_utc": expires.isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "formal10_authorized": False,
        "collected_by_main_agent": True,
        "remote_mutation_performed": False,
        "host": {
            "host_id": expected["host_id"],
            "ssh_target": expected["ssh_target"],
            "address": expected["address"],
        },
        "storage": {
            "project_path": expected["project"],
            "mount_path": expected["mount"],
            "template_path": expected["template"],
            "device": expected["device"],
            "device_major_minor": expected["major_minor"],
            "mount_source_verified": True,
            "filesystem": expected["filesystem"],
            "template_tree_verified": True,
            "exact_row_counts_verified": True,
            "clean_shutdown": True,
            "no_live_template_owner": True,
            "full_physical_copy_no_reflink": True,
        },
        "resource": {
            "cpuset_cpus": expected["cpuset"],
            "cpuset_mems": expected["mems"],
            **expected["envelope"],
            "physical_core_siblings_verified": True,
            "numa_distance_verified": True,
            "live_cgroup_readback": True,
        },
        "mysql": {
            "planner_host_port": expected["planner_port"],
            "replay_host_port": expected["replay_port"],
            "container_port": 3306,
            "process_affinity": expected["cpuset"],
            "buffer_pool_bytes":
                expected["envelope"]["buffer_pool_bytes"],
            "fixed_settings_read_back": True,
            "database_counter_timeout_policy": "FAIL",
        },
        "runtime": runtime,
        "co_resident_inventory": {
            "complete": True,
            "postgresql_inventoried_and_untouched": True,
            "database_and_benchmark_processes_complete": True,
            "ports_free_before_launch": True,
        },
        "raw_artifacts": {"live.raw": sha(live_raw)},
    }
    live_path = root / "live.json"
    write_json(live_path, live)

    static_raw = root / "static.raw"
    static_raw.write_text("static gate raw\n", encoding="utf-8")
    static = {
        "schema_version":
            "mysql_algorithm_c_distributed_static_gate_evidence_v1",
        "status": "PASS",
        "scenario": expected["scenario"],
        "peer_scenario": expected["peer_scenario"],
        "host_id": expected["host_id"],
        "attempt_token": attempt,
        "captured_at_utc": captured.isoformat(),
        "expires_at_utc": expires.isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "formal10_authorized": False,
        "checks": {key: True for key in verifier.STATIC_CHECKS},
        "co_resident_inventory_sha256": sha(live_path),
        "raw_artifacts": {"static.raw": sha(static_raw)},
    }
    static_path = root / "static.json"
    write_json(static_path, static)

    dynamic_raw = root / "dynamic.raw"
    dynamic_raw.write_text("dynamic gate raw\n", encoding="utf-8")
    dynamic = {
        "schema_version":
            "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
        "status": "PASS",
        "scenario": expected["scenario"],
        "peer_scenario": expected["peer_scenario"],
        "host_id": expected["host_id"],
        "attempt_token": attempt,
        "captured_at_utc": captured.isoformat(),
        "expires_at_utc": expires.isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "formal10_authorized": False,
        "policy_version":
            "mysql_algorithm_c_same_host_bidirectional_interference_gate_v1",
        "duration_seconds": 180,
        "sample_period_seconds": 1,
        "minimum_samples_per_stream": 181,
        "directions_passed": sorted(verifier.DYNAMIC_DIRECTIONS),
        "streams_complete": sorted(verifier.DYNAMIC_STREAMS),
        "database_counter_missing_samples": 0,
        "database_counter_timeout_count": 0,
        "all_tolerances_passed": True,
        "ongoing_monitor_required": True,
        "authorization_reuse": "PROHIBITED_ACROSS_ATTEMPTS",
        "qualification_capture_r0_formal_accounting": "PROHIBITED",
        "raw_artifacts": {"dynamic.raw": sha(dynamic_raw)},
    }
    dynamic_path = root / "dynamic.json"
    write_json(dynamic_path, dynamic)
    return live_path, static_path, dynamic_path


def test_runtime_manifests_are_recomputed_and_executable_bound() -> None:
    result = verifier.verify_runtime_manifests()
    assert result["python_tree_sha256"] == verifier.PYTHON_TREE_SHA256
    assert (
        result["python_tree_content_sha256"]
        == verifier.PYTHON_TREE_CONTENT_SHA256
    )
    assert (
        result["python_executable_sha256"]
        == verifier.PYTHON_EXECUTABLE_SHA256
    )
    assert result["jdk_tree_sha256"] == verifier.JDK_TREE_SHA256
    assert (
        result["jdk_tree_content_sha256"]
        == verifier.JDK_TREE_CONTENT_SHA256
    )
    assert (
        result["java_executable_sha256"]
        == verifier.JAVA_EXECUTABLE_SHA256
    )


@pytest.mark.parametrize("scenario", ["job", "tpch"])
def test_debug_qualification_requires_live_and_static_same_attempt(
    scenario: str, tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    live, static, _ = evidence_set(tmp_path, scenario, now)
    result = verifier.verify_promotion_evidence(
        scenario=scenario,
        mode="debug_qualification",
        live_readback_path=live,
        static_gate_path=static,
        now=now,
    )
    assert result["debug_qualification_launch_authorized"] is True
    assert result["fresh_r0_launch_authorized"] is False
    assert result["formal10_authorized"] is False


@pytest.mark.parametrize("scenario", ["job", "tpch"])
def test_fresh_r0_requires_dynamic_bidirectional_gate(
    scenario: str, tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    live, static, dynamic = evidence_set(tmp_path, scenario, now)
    with pytest.raises(verifier.EvidenceError, match="requires dynamic"):
        verifier.verify_promotion_evidence(
            scenario=scenario,
            mode="fresh_r0",
            live_readback_path=live,
            static_gate_path=static,
            now=now,
        )
    result = verifier.verify_promotion_evidence(
        scenario=scenario,
        mode="fresh_r0",
        live_readback_path=live,
        static_gate_path=static,
        dynamic_gate_path=dynamic,
        now=now,
    )
    assert result["fresh_r0_launch_authorized"] is True
    assert result["accepted_round_authorized"] is False


def test_wrong_runtime_stale_evidence_and_counter_gaps_fail_closed(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    live, static, dynamic = evidence_set(tmp_path, "tpch", now)
    document = json.loads(live.read_text())
    document["runtime"]["python"]["tree_content_sha256"] = "0" * 64
    write_json(live, document)
    static_document = json.loads(static.read_text())
    static_document["co_resident_inventory_sha256"] = sha(live)
    write_json(static, static_document)
    with pytest.raises(verifier.EvidenceError, match="Python runtime"):
        verifier.verify_promotion_evidence(
            scenario="tpch",
            mode="fresh_r0",
            live_readback_path=live,
            static_gate_path=static,
            dynamic_gate_path=dynamic,
            now=now,
        )

    live, static, dynamic = evidence_set(tmp_path, "tpch", now)
    document = json.loads(live.read_text())
    del document["runtime"]["python"]["exact_entry_set_verified"]
    write_json(live, document)
    static_document = json.loads(static.read_text())
    static_document["co_resident_inventory_sha256"] = sha(live)
    write_json(static, static_document)
    with pytest.raises(verifier.EvidenceError, match="Python runtime"):
        verifier.verify_promotion_evidence(
            scenario="tpch",
            mode="fresh_r0",
            live_readback_path=live,
            static_gate_path=static,
            dynamic_gate_path=dynamic,
            now=now,
        )

    live, static, dynamic = evidence_set(tmp_path, "tpch", now)
    dynamic_document = json.loads(dynamic.read_text())
    dynamic_document["database_counter_missing_samples"] = 1
    write_json(dynamic, dynamic_document)
    with pytest.raises(verifier.EvidenceError, match="dynamic interference"):
        verifier.verify_promotion_evidence(
            scenario="tpch",
            mode="fresh_r0",
            live_readback_path=live,
            static_gate_path=static,
            dynamic_gate_path=dynamic,
            now=now,
        )

    stale_now = now + timedelta(hours=1)
    with pytest.raises(verifier.EvidenceError, match="stale"):
        verifier.verify_promotion_evidence(
            scenario="tpch",
            mode="debug_qualification",
            live_readback_path=live,
            static_gate_path=static,
            now=stale_now,
        )


def test_promotion_builder_is_content_addressed_idempotent_and_never_formal10(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    evidence_root = tmp_path / "input"
    evidence_root.mkdir()
    live, static, dynamic = evidence_set(evidence_root, "job", now)
    output = {"job": tmp_path / "job", "tpch": tmp_path / "tpch"}
    monkeypatch.setattr(promotion, "OUTPUT_ROOTS", output)
    first = promotion.build_promotion(
        scenario="job",
        mode="fresh_r0",
        live_readback=live,
        static_gate=static,
        dynamic_gate=dynamic,
        execute=True,
        now=now,
    )
    second = promotion.build_promotion(
        scenario="job",
        mode="fresh_r0",
        live_readback=live,
        static_gate=static,
        dynamic_gate=dynamic,
        execute=True,
        now=now,
    )
    assert first == second
    bundle = Path(first["bundle_directory"])
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    config = json.loads((bundle / "config.json").read_text())
    assert hashlib.sha256((bundle / "closure.json").read_bytes()).hexdigest() == bundle.name
    promotion.staged.verify_file_map(
        bundle, manifest["files"], "promotion test"
    )
    assert manifest["fresh_r0_launch_authorized"] is True
    assert manifest["formal10_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert config["fresh_r0_only"] is True
    assert config["outer_execution_authorized"] is False
    assert config["formal_outer_protocol"]["max_outer_attempts"] == 0
    assert config["formal_outer_protocol"]["target_accepted_shrinks"] == 0
    assert config["runtime_authority"]["interpreter_path"].endswith(
        verifier.PYTHON_TREE_CONTENT_SHA256 + "/bin/python"
    )
    assert (bundle / "source/source_manifest.json").is_file()
    assert (bundle / "staged/bundle_manifest.json").is_file()
    assert not list(tmp_path.rglob("*.tmp.*"))


def test_qualification_bundle_does_not_start_formal_campaign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    evidence_root = tmp_path / "input"
    evidence_root.mkdir()
    live, static, _ = evidence_set(evidence_root, "tpch", now)
    monkeypatch.setattr(
        promotion,
        "OUTPUT_ROOTS",
        {"job": tmp_path / "job", "tpch": tmp_path / "tpch"},
    )
    result = promotion.build_promotion(
        scenario="tpch",
        mode="debug_qualification",
        live_readback=live,
        static_gate=static,
        execute=True,
        now=now,
    )
    bundle = Path(result["bundle_directory"])
    launched = subprocess.run(
        [sys.executable, str(bundle / promotion.LAUNCHER["tpch"])],
        text=True,
        capture_output=True,
        check=False,
    )
    assert launched.returncode == 0, launched.stderr
    output = json.loads(launched.stdout)
    assert output["status"] == "DEBUG_QUALIFICATION_AUTHORIZED"
    assert output["formal_campaign_started"] is False


def test_promoted_service_explicitly_binds_distributed_campaign_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass(frozen=True)
    class Lane:
        host: str
        hostname: str
        project: str
        template: str
        device: str
        cpuset: str
        mems: str
        cpu_cores: float
        memory_bytes: int
        pids: int
        read_bps: int
        write_bps: int
        remote_port: int
        buffer_pool_bytes: int

    class Services:
        def __init__(self, _config: dict) -> None:
            self.lane = Lane(
                "wrong", "wrong", "/wrong", "/wrong", "/dev/wrong",
                "0", "0", 99.0, 1, 1, 1, 1, 1, 1,
            )
            self.replay_lane = self.lane

        def bundle(self) -> dict:
            return {"lane": self.lane, "replay_lane": self.replay_lane}

    fake = types.ModuleType("mysql_algorithm_b_production_services")
    fake.ProductionServices = Services
    monkeypatch.setitem(
        sys.modules, "mysql_algorithm_b_production_services", fake
    )
    authority = {
        "mode": "fresh_r0",
        "attempt_token": "tpch-attempt-20260729-abcdef0123456789",
        "fresh_r0_launch_authorized": True,
    }
    payload = promotion.service_payload("tpch", authority)
    namespace: dict = {}
    exec(compile(payload, "promoted_service.py", "exec"), namespace)
    config = {
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scenario": "tpch_memory",
        "formal10_authorized": False,
        "accepted_round_authorized": False,
        "outer_execution_authorized": False,
        "fresh_r0_only": True,
        "production": {
            "service_factory": promotion.SERVICE,
            "launch_authorized": True,
        },
        "promotion_authority": {
            "mode": authority["mode"],
            "attempt_token": authority["attempt_token"],
            "authority_sha256":
                promotion.sha_bytes(promotion.canonical(authority)),
        },
    }
    result = namespace["build"](config)
    expected = verifier.EXPECTED["tpch"]
    lane = result["lane"]
    replay = result["replay_lane"]
    assert lane.host == expected["ssh_target"]
    assert lane.hostname == expected["host_id"]
    assert lane.project == expected["project"]
    assert lane.template == expected["template"]
    assert lane.device == expected["device"]
    assert lane.cpuset == expected["cpuset"]
    assert lane.mems == expected["mems"]
    assert lane.remote_port == 65103
    assert replay.remote_port == 65203
