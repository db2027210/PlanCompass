from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "shell/build_mysql_xp_formal10_successor_v2.py"
READINESS_PATH = ROOT / "shell/mysql_xp_formal10_readiness_v2.py"
BASE = (
    ROOT
    / "artifacts/mysql_xp_distributed_successor_v1"
    / "ea1c826ffa33b409bb94df04eec0d45c70702b80c7c48ff44ac0ba557e20f1b9"
)


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


builder = module(BUILDER_PATH, "xp_formal10_builder_v2")
readiness = module(READINESS_PATH, "xp_formal10_readiness_v2")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    output = tmp_path_factory.mktemp("xp-formal10-v2")
    first = builder.publish(BASE, output)
    second = builder.publish(BASE, output)
    assert first == second
    return first


def test_transactional_idempotent_frozen_bundle(built):
    manifest = builder.verify_bundle(built)
    assert manifest["publication"] == {
        "atomic_directory_rename": True,
        "all_entries_frozen_before_visibility": True,
        "concurrent_identical_publish_idempotent": True,
        "content_addressed": True,
        "fsync_before_and_after_rename": True,
        "non_overwriting": True,
        "same_filesystem": True,
    }
    for path in [built, *built.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0


def test_static_readiness_and_exact_algorithm_contract(built):
    result = readiness.verify_bundle(built)
    contract = result["contract"]
    assert contract["algorithm"]["active_parameter_count"] == 7
    assert contract["algorithm"]["required_nonempty_subsets"] == 127
    assert contract["algorithm"]["fixed_real_replay_top_k"] == 5
    assert contract["algorithm"]["target_accepted_rounds"] == 10
    assert contract["algorithm"]["original_gradient_only"] is True
    assert contract["algorithm"]["required_gradient_event_fields"] == (
        readiness.EXPECTED_GRADIENT_FIELDS
    )
    assert contract["database_resource_envelope"] == (
        readiness.EXPECTED_DB_ENVELOPE
    )
    assert contract["client_resource_envelope"] == (
        readiness.EXPECTED_CLIENT_ENVELOPE
    )


def test_worker_future_and_xapclient_are_code_not_manifest_only(built):
    client_source = (
        built
        / "predecessor/predecessor/source/src/main/java/"
        "com/hybench/workload/Client.java"
    ).read_text(encoding="utf-8")
    assert "cs.take()" in client_source
    assert ".get();" in client_source
    assert "load worker Future is absent or cancelled" in client_source
    hybench_source = (
        built
        / "predecessor/predecessor/source/src/main/java/"
        "com/hybench/HyBench.java"
    ).read_text(encoding="utf-8")
    assert 'ConfigLoader.prop.getProperty("xtpclient","-1")' in hybench_source
    assert 'ConfigLoader.prop.getProperty("xapclient","-1")' in hybench_source
    assert 'if(apClient.equalsIgnoreCase("-1"))' in hybench_source
    assert 'Missing configuration xapclient' in hybench_source
    assert 'if(tpClient.equalsIgnoreCase("-1"))' in hybench_source
    assert 'if(tpClient.equalsIgnoreCase("-1")) {\n            logger.error("Missing configuration xapclient")' not in hybench_source


def test_manifest_covers_required_runtime_and_dependency_closure(built):
    v8 = json.loads(
        (
            built / "predecessor/predecessor/manifest.json"
        ).read_text(encoding="utf-8")
    )
    closure = v8["formal_runtime_closure"]
    assert {
        "campaign",
        "config_template",
        "connector",
        "dependency_classpath",
        "launch_contract",
        "optimizer",
        "orchestration",
        "pom",
        "profile",
        "source_pom",
        "statement",
    } <= set(closure)
    assert len(v8["dependency_closure"]) > 0
    for value in closure.values():
        path = built / "predecessor/predecessor" / value["path"]
        assert path.is_file()
        assert builder.sha256(path) == value["sha256"]


def test_native_latency_producer_is_real_histogram_and_adapter_fails_closed(built):
    source = (
        built
        / "predecessor/qualification/native_latency_producer/source/"
        "HyBenchFormalMain.java"
    ).read_text(encoding="utf-8")
    assert "getPercentile(percentile)" in source
    assert "HYBENCH_FORMAL_SAMPLE_JSON=" in source
    assert '\\"latency_p50_ms\\"' in source
    assert '\\"latency_p95_ms\\"' in source
    assert '\\"latency_p99_ms\\"' in source
    adapter = (
        built
        / "qualification/"
        "mysql_distributed_workload_metric_adapter_v1.py"
    ).read_text(encoding="utf-8")
    assert "native workload marker is absent" in adapter
    assert "native workload marker is stale" in adapter
    assert "native latency percentiles are unordered" in adapter
    assert "estimated" not in adapter.split("def _last_marker", 1)[1].split(
        "def _append_jsonl", 1
    )[0]


def test_current_shared_qualification_runtime_is_sealed_directly(built):
    expected = {
        "mysql_distributed_live_readback_qualification_v1.py":
            ROOT / "shell/mysql_distributed_live_readback_qualification_v1.py",
        "mysql_distributed_workload_metric_adapter_v1.py":
            ROOT / "shell/mysql_distributed_workload_metric_adapter_v1.py",
        "runtime_tree_content_identity_v3.py":
            ROOT / "shell/runtime_tree_content_identity_v3.py",
    }
    for name, source in expected.items():
        sealed = built / "qualification" / name
        assert sealed.is_file()
        assert builder.sha256(sealed) == builder.sha256(source)
    collector = (
        built
        / "qualification/mysql_distributed_live_readback_qualification_v1.py"
    ).read_text(encoding="utf-8")
    assert "docker_mount_and_cgroup_fallback" in collector
    assert "docker_cgroup_aggregate_fallback" in collector
    adapter = (
        built
        / "qualification/mysql_distributed_workload_metric_adapter_v1.py"
    ).read_text(encoding="utf-8")
    assert "DEBUG_SQL_METRIC_SAMPLE_JSON" in adapter


def test_promotion_is_fail_closed_without_every_real_evidence(built):
    with pytest.raises(readiness.ReadinessError, match="all promotion evidence"):
        readiness.main(["--bundle", str(built)])


def test_static_mode_never_grants_formal_authority(built, capsys):
    assert readiness.main(
        ["--bundle", str(built), "--static-only"]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "STATIC_PASS_EVIDENCE_PENDING"
    assert result["formal_launch_authorized"] is False
    assert result["accepted_round_authorized"] is False
