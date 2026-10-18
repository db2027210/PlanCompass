from pathlib import Path
import subprocess

from scripts import reset_mysql_xp_data_planes_client_primary_v5 as subject


def test_only_exact_docker_start_already_exists_is_recoverable() -> None:
    exact = subprocess.CalledProcessError(
        1, ["docker", "start", "owned"],
        stderr="failed to create task: task abc already exists",
    )
    other = subprocess.CalledProcessError(
        1, ["docker", "start", "owned"], stderr="permission denied",
    )
    assert subject._is_stale_task_start(exact, "owned")
    assert not subject._is_stale_task_start(exact, "other")
    assert not subject._is_stale_task_start(other, "owned")


def test_verified_stale_task_is_removed_then_staged_reset_is_reentered(monkeypatch) -> None:
    calls = []
    attempts = 0
    container_id = "a" * 64

    def fake_prepare(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        calls.append((args, kwargs))
        if attempts == 1:
            raise subprocess.CalledProcessError(
                1, ["docker", "start", "owned"],
                stderr="failed to create task: task abc already exists",
            )
        return {"ready": True}

    def fake_json(*args):
        assert args == ("inspect", "owned")
        return {
            "Id": container_id,
            "State": {
                "Status": "exited", "Running": False, "Pid": 0,
                "Error": "failed to create task: task abc already exists",
            },
            "Config": {"Labels": {
                "ra.owner": "mysql_four_scene_v5", "ra.worker_id": "xp_cpu_w1",
            }},
            "Mounts": [{
                "Destination": "/var/lib/mysql",
                "Source": "/disk2/test/xp_cpu_w1/datadir",
            }],
        }

    def fake_run(argv, *, timeout):
        calls.append((argv, timeout))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subject, "_PREDECESSOR_PREPARE", fake_prepare)
    monkeypatch.setattr(subject.implementation, "_docker_json", fake_json)
    monkeypatch.setattr(subject.implementation, "_docker_run", fake_run)
    row = subject._prepare_fresh_xp(
        {},
        {"worker_id": "xp_cpu_w1", "container": "owned",
         "runtime_root": "/disk2/test/xp_cpu_w1"},
        {"container_id": container_id}, Path("/tmp/quarantine/run/token"),
        Path("/tmp/output"), False, "formal_point",
    )
    assert row["ready"] is True
    assert row["stale_containerd_task_recovery"]["container_record_removed"] is True
    assert ["docker", "rm", "-f", "owned"] in [
        call[0] for call in calls if isinstance(call[0], list)
    ]
    assert attempts == 2
    assert calls[-1][1]["allow_absent_predecessor_after_interrupted_discard"] is True
