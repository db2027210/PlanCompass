from pathlib import Path
import subprocess

from scripts import reset_mysql_xp_data_planes_client_primary_v6 as subject


def test_legacy_freeze_runtime_root_is_used_for_stale_task_recovery(monkeypatch) -> None:
    attempts = 0
    container_id = "b" * 64

    def fake_prepare(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise subprocess.CalledProcessError(
                1, ["docker", "start", "owned"],
                stderr="failed to create task: task abc already exists",
            )
        return {"ready": True}

    monkeypatch.setattr(subject, "_PREDECESSOR_PREPARE", fake_prepare)
    monkeypatch.setattr(subject.implementation, "_docker_json", lambda *args: {
        "Id": container_id,
        "State": {"Running": False, "Pid": 0,
                  "Error": "task abc already exists"},
        "Config": {"Labels": {"ra.owner": "mysql_four_scene_v5",
                              "ra.worker_id": "xp_cpu_w1"}},
        "Mounts": [{"Destination": "/var/lib/mysql",
                    "Source": "/disk2/legacy/xp_cpu_w1/datadir"}],
    })
    monkeypatch.setattr(
        subject.implementation, "_docker_run",
        lambda argv, *, timeout: subprocess.CompletedProcess(argv, 0, stdout=""),
    )
    row = subject._prepare_fresh_xp(
        {"storage": {"runtime_root": "/disk2/legacy"}},
        {"worker_id": "xp_cpu_w1", "container": "owned"},
        {"container_id": container_id}, Path("/tmp/quarantine/run/token"),
        Path("/tmp/output"), False, "formal_point",
    )
    assert row["ready"] is True
    assert row["stale_containerd_task_recovery"]["datadir_preserved"] == (
        "/disk2/legacy/xp_cpu_w1/datadir"
    )
