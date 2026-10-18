from scripts import mysql_algorithm_c_reset_affinity_v4 as subject


def test_post_restart_enforcement_uses_returned_reset_identity(monkeypatch):
    predecessor = {"container": "xp_cpu_w2", "container_id": "old-id"}
    successor = {"container": "xp_cpu_w2", "container_id": "new-id"}
    monkeypatch.setattr(
        subject.predecessor,
        "prepare_fresh_xp",
        lambda *args, **kwargs: successor,
    )
    seen = []

    def enforce(worker):
        seen.append(worker["container_id"])
        return {"container_id": worker["container_id"], "after": 0}

    monkeypatch.setattr(subject, "_enforce_swappiness", enforce)

    result = subject.prepare_fresh_xp("freeze", predecessor)

    assert seen == ["new-id"]
    assert result["post_restart_swappiness_readback"]["container_id"] == "new-id"
    assert successor == {"container": "xp_cpu_w2", "container_id": "new-id"}
