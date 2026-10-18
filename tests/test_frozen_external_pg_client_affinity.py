from __future__ import annotations

import json
from pathlib import Path

from scripts import enforce_frozen_external_pg_client_affinity_v1 as guard


def test_declared_client_cpuset_must_not_overlap_mysql(
    tmp_path: Path, monkeypatch,
) -> None:
    disk1 = tmp_path / "disk1/resource_adaptive"
    config = disk1 / "campaign/xp_cpu/optimizer_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"algorithm_c": {"controller_client_cpuset": "128-131"}}))
    monkeypatch.setattr(guard, "DISK1_ROOT", disk1)
    assert guard.load_declared_cpuset(config) == frozenset(range(128, 132))

    config.write_text(json.dumps({"algorithm_c": {"controller_client_cpuset": "112-119"}}))
    try:
        guard.load_declared_cpuset(config)
    except RuntimeError as exc:
        assert "overlaps MySQL" in str(exc)
    else:
        raise AssertionError("overlapping external cpuset was accepted")


def test_cohort_support_client_uses_its_scene_declaration(
    tmp_path: Path, monkeypatch,
) -> None:
    disk1 = tmp_path / "disk1/resource_adaptive"
    campaign = disk1 / "campaign"
    config = campaign / "xp_cpu/optimizer_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"algorithm_c": {"controller_client_cpuset": "128-131"}}))
    monkeypatch.setattr(guard, "DISK1_ROOT", disk1)
    commands = {
        10: f"python supervisor.py --campaign-root {campaign}",
        11: (
            "java -cp lib/* com.hybench.HyBench -t runxp -c "
            f"{campaign}/cohort_support/xp_cpu/wave_1/workload.props "
            f"-f {disk1}/native/conf/stmt_postgres.toml"
        ),
    }
    roots, markers = guard.discover_domains(commands)
    desired = guard.desired_client_cpus(11, commands[11], roots, markers)
    assert desired == frozenset(range(128, 132))


def test_unrelated_disk1_client_and_postgres_backend_are_never_modified() -> None:
    cpuset = frozenset(range(128, 132))
    markers = [("/disk1/resource_adaptive/campaign/xp_cpu/", cpuset)]
    assert guard.desired_client_cpus(
        20, "java -jar unrelated.jar", {}, markers,
    ) is None
    assert guard.desired_client_cpus(
        21, "postgres: postgres db 127.0.0.1 SELECT", {21: cpuset}, markers,
    ) is None


def test_native_xp_wrapper_is_pinned_before_java_exec() -> None:
    cpuset = frozenset(range(128, 132))
    marker = "/disk1/resource_adaptive/campaign/cohort_support/xp_cpu/"
    command = (
        "taskset -c 0-511 /disk1/resource_adaptive/native/runxp "
        f"-c {marker}wave_31/workload.props "
        "-f /disk1/resource_adaptive/native/conf/stmt_postgres.toml"
    )
    assert guard.desired_client_cpus(
        31, command, {}, [(marker, cpuset)],
    ) == cpuset
