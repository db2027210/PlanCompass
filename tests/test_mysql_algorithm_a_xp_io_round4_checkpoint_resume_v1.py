from __future__ import annotations

import copy
from pathlib import Path

from scripts import prepare_mysql_algorithm_a_xp_io_round4_after_c_v1 as transition
from scripts import run_mysql_algorithm_a_xp_io_round4_checkpoint_resume_v1 as resume


def test_round4_checkpoint_is_valid_and_contains_no_physical_credit() -> None:
    state, candidates = resume.validate_predecessor()
    assert state["round_index"] == 4
    assert state["accepted"] == 3
    assert state["round_checkpoint"]["attempted_ranks"] == []
    assert state["round_checkpoint"]["waves"] == []
    assert len(resume.candidate_vectors(candidates)) == 16


def test_round4_candidate_equivalence_covers_executable_top4_vectors() -> None:
    _state, candidates = resume.validate_predecessor()
    expected = resume.candidate_vectors(candidates)
    changed = copy.deepcopy(candidates)
    changed["candidates"][0]["optimized_params"][resume.ACTIVE[0]] *= 2
    assert resume.candidate_vectors(candidates) == expected
    assert resume.candidate_vectors(changed) != expected


def test_a_remains_check_only_and_runtime_dispatch_is_eight_worker() -> None:
    reuse = resume.load(resume.REUSE_MANIFEST)
    assert reuse["algorithm_a"]["feedback_to_b_or_c"] is False
    assert reuse["algorithm_a"]["candidate_vectors_used_by_b_or_c"] is False
    runtime_path = Path(resume.runtime.__file__).resolve()
    source = runtime_path.read_text(encoding="utf-8")
    xp8_v3 = runtime_path.with_name(
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v3.py"
    ).read_text(encoding="utf-8")
    assert runtime_path.name == (
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v12.py"
    )
    assert "reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6" in source
    assert '"base.REPLAY_PARALLELISM = 4": "base.REPLAY_PARALLELISM = 8"' in xp8_v3
    assert resume.EXPECTED == {f"xp_io_w{i}" for i in range(1, 9)}


def test_post_c_inventory_allows_resource_difference_before_a_reset(
    tmp_path: Path, monkeypatch,
) -> None:
    frozen_workers = []
    source_workers = []
    inspected = {}
    cgroup_root = tmp_path / "blkio" / "docker-limited"
    for index in range(1, 9):
        worker_id = f"xp_io_w{index}"
        container = f"container-{index}"
        runtime = tmp_path / worker_id
        datadir = runtime / "data"
        credentials = runtime / "credentials"
        datadir.mkdir(parents=True)
        credentials.mkdir()
        (credentials / "selected.cnf").write_text("[client]\n")
        (credentials / "benchmark.cnf").write_text("[client]\n")
        frozen_workers.append({
            "worker_id": worker_id,
            "scene": transition.SCENE,
            "database_cpuset": str(index),
        })
        container_id = f"{index:064x}"
        container_pid = 1000 + index
        source_workers.append({
            "worker_id": worker_id,
            "scene": transition.SCENE,
            "container": container,
            "blkio_authority": {"device_major_minor": "259:5"},
        })
        cgroup = cgroup_root / container_id
        cgroup.mkdir(parents=True)
        (cgroup / "tasks").write_text(f"{container_pid}\n")
        (cgroup / "blkio.throttle.read_bps_device").write_text(
            "259:5 1205862400\n"
        )
        (cgroup / "blkio.throttle.write_bps_device").write_text(
            "259:5 1205862400\n"
        )
        inspected[container] = {
            "Id": container_id,
            "State": {"Running": True, "Pid": container_pid},
            "Config": {"Labels": {"ra.owner": "mysql_four_scene_v5"}},
            "HostConfig": {
                "CpusetCpus": str(index),
                "CpusetMems": "0",
                # Deliberately C's values, not the later A target values.
                "CpuPeriod": 100000,
                "CpuQuota": 170000,
                "Memory": 8589934592,
                "MemorySwap": 8589934592,
                "PidsLimit": 4096,
            },
            "Mounts": [
                {"Destination": "/var/lib/mysql", "Source": str(datadir)},
                {"Destination": "/run/ra-credentials", "Source": str(credentials)},
            ],
        }

    def fake_load(path: Path):
        if path == transition.FREEZE:
            return {
                "schema_version": "mysql_xp_eight_worker_resource_successor_v1",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "workers": frozen_workers,
            }
        if path == transition.SOURCE_AUTHORITY:
            return {"status": "PREPARED", "workers": source_workers}
        raise AssertionError(path)

    class MustNotRunBeforeReset:
        def _controller_path(self, pid, container_id, *_args):
            assert pid > 1
            return cgroup_root / container_id

        def _target_device_rate(self, path, device):
            for line in path.read_text().splitlines():
                key, value = line.split()
                if key == device:
                    return int(value)
            return None

        def _kernel_resource_readback(self, *_args, **_kwargs):
            raise AssertionError("A resource readback ran before transition reset")

        def _enforce_blkio(self, *_args, **_kwargs):
            raise AssertionError("A blkio enforcement ran before transition reset")

    monkeypatch.setattr(transition, "load", fake_load)
    monkeypatch.setattr(transition.reset8, "install_for_scene", lambda _rows: None)
    monkeypatch.setattr(transition.reset8, "configure", lambda **_kwargs: None)
    monkeypatch.setattr(transition, "BLKIO_LIMITED_ROOT", cgroup_root)
    impl = transition.reset8.predecessor.predecessor.predecessor.base._impl
    monkeypatch.setitem(impl, "_docker_json", lambda _verb, name: inspected[name])
    monkeypatch.setitem(impl, "prep", MustNotRunBeforeReset())

    _freeze, rows = transition.current_predecessor_rows()
    assert set(rows) == transition.EXPECTED
    for row in rows.values():
        observation = row["pre_transition_docker_resource_observation"]
        assert observation["cpu_quota"] == 170000
        assert observation["memory"] == 8589934592
        assert observation["authorizes_algorithm_a_workload"] is False
        assert observation["restored_and_validated_by_transition_reset"] is True
        assert observation[
            "discard_blkio_authority_refreshed_to_current_container"
        ] is True
        assert row["blkio_authority"]["status"] == (
            "CURRENT_POST_C_DISCARD_AUTHORITY"
        )
        assert row["blkio_authority"]["read_bps"] == 1205862400
        assert row["blkio_authority"]["write_bps"] == 1205862400
        assert "kernel_cgroup_resources" not in row


def test_post_c_inventory_accepts_both_throttles_already_lifted(
    tmp_path: Path, monkeypatch,
) -> None:
    worker_id = "xp_io_w1"
    container = "container-1"
    container_id = "1" * 64
    container_pid = 1001
    runtime = tmp_path / worker_id
    datadir = runtime / "data"
    credentials = runtime / "credentials"
    datadir.mkdir(parents=True)
    credentials.mkdir()
    (credentials / "selected.cnf").write_text("[client]\n")
    (credentials / "benchmark.cnf").write_text("[client]\n")
    cgroup_root = tmp_path / "blkio" / "docker-limited"
    cgroup = cgroup_root / container_id
    cgroup.mkdir(parents=True)
    (cgroup / "tasks").write_text(f"{container_pid}\n")
    (cgroup / "blkio.throttle.read_bps_device").write_text("")
    (cgroup / "blkio.throttle.write_bps_device").write_text("")
    inspected = {
        "Id": container_id,
        "State": {"Running": True, "Pid": container_pid},
    }
    row = {
        "blkio_authority": {
            "device_major_minor": "259:5",
            "read_bps": 1205862400,
            "write_bps": 1205862400,
        },
    }

    class Prep:
        def _controller_path(self, *_args):
            return cgroup

        def _target_device_rate(self, _path, _device):
            return None

    monkeypatch.setattr(transition, "BLKIO_LIMITED_ROOT", cgroup_root)
    authority = transition.current_discard_blkio_authority(
        worker_id, row, inspected, {"prep": Prep()},
    )
    assert authority["status"] == (
        "CURRENT_POST_C_ALREADY_LIFTED_DISCARD_AUTHORITY"
    )
    assert authority["already_lifted_before_transition"] is True
    assert authority["read_bps"] == 1205862400
    assert authority["write_bps"] == 1205862400
