from pathlib import Path as RealPath

from scripts import mysql_algorithm_c_reset_affinity_v2 as subject


def worker() -> dict:
    return {
        "container": "xp_cpu_w2",
        "container_id": "frozen-container-id",
        "database_cpuset": "144-159",
        "effective_resources": {"MemorySwappiness": 0},
    }


def test_reapplies_and_reads_back_swappiness_after_restart(tmp_path, monkeypatch):
    cgroup = tmp_path / "frozen-container-id" / "memory.swappiness"
    cgroup.parent.mkdir()
    cgroup.write_text("60\n", encoding="ascii")
    monkeypatch.setattr(subject, "_live_container_id", lambda _: "frozen-container-id")
    monkeypatch.setattr(subject, "Path", lambda _: tmp_path)

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        RealPath(command[-1]).write_text(kwargs["input"], encoding="ascii")

        class Completed:
            pass

        return Completed()

    monkeypatch.setattr(subject.subprocess, "run", run)
    result = subject._enforce_swappiness(worker())

    assert result["before"] == 60
    assert result["after"] == 0
    assert result["reapplied"] is True
    assert result["readback_passed"] is True
    assert calls == [[
        "taskset", "-c", "144-159", "sudo", "-n", "tee", str(cgroup)
    ]]


def test_prepare_fresh_xp_appends_readback_without_mutating_predecessor(
    monkeypatch,
):
    original = {"status": "PASS"}
    monkeypatch.setattr(
        subject.predecessor, "prepare_fresh_xp", lambda *args, **kwargs: original
    )
    monkeypatch.setattr(
        subject, "_enforce_swappiness", lambda _: {"after": 0, "readback_passed": True}
    )

    result = subject.prepare_fresh_xp("scene", worker())

    assert result["post_restart_swappiness_readback"] == {
        "after": 0, "readback_passed": True
    }
    assert original == {"status": "PASS"}
