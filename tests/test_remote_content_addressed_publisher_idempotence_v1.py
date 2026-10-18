from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "shell/publish_remote_content_addressed_bundle_v1.py"
JOB = (
    ROOT
    / "artifacts/mysql_job_raw_all_subset_three_level_successor_v4"
    / "d6eddbd219c089e930686264e4099f1ced4623e481ecbb348e7b0345858629f3"
)


def module():
    spec = importlib.util.spec_from_file_location("publisher_v1", PUBLISHER)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_exact_visible_bundle_is_idempotent_without_scp(monkeypatch):
    publisher = module()
    parent = "/mnt/ssd/bundles"
    final = parent + "/" + JOB.name
    calls = []

    def remote_python(host, arguments, source):
        calls.append(("remote_python", host, arguments, source))
        return "REMOTE_FINAL_PRESENT_DIRECTORY " + final + "\n"

    def remote_verify(host, path, bundle_id, **_kwargs):
        calls.append(("remote_verify", host, path, bundle_id))
        return "REMOTE_BUNDLE_EXACT_PASS %s 103\n" % bundle_id

    monkeypatch.setattr(publisher, "remote_python", remote_python)
    monkeypatch.setattr(publisher, "remote_verify", remote_verify)
    monkeypatch.setattr(
        publisher,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scp must not run for an exact visible bundle")
        ),
    )
    result = publisher.publish(JOB, "user@host", parent)
    assert result["status"] == "PASS_ALREADY_PRESENT_EXACT"
    assert result["idempotent_no_write"] is True
    assert result["staged_verification"] is None
    assert [row[0] for row in calls] == ["remote_python", "remote_verify"]


def test_absent_visible_bundle_enters_staged_atomic_path(monkeypatch):
    publisher = module()
    parent = "/mnt/ssd/bundles"
    final = parent + "/" + JOB.name
    events = []

    def remote_python(host, arguments, source):
        if source == publisher.REMOTE_PROBE_FINAL:
            events.append("probe")
            return "REMOTE_FINAL_ABSENT " + final + "\n"
        if source == publisher.REMOTE_PREPARE:
            events.append("prepare")
            return "REMOTE_DIRECT_STAGE_READY\n"
        if source == publisher.REMOTE_PUBLISH:
            events.append("publish")
            return "REMOTE_BUNDLE_ATOMICALLY_PUBLISHED " + final + "\n"
        raise AssertionError("unexpected remote source")

    def remote_verify(_host, path, bundle_id, **kwargs):
        events.append(
            "verify_stage" if kwargs.get("allow_staging_name") else "verify_final"
        )
        return "REMOTE_BUNDLE_EXACT_PASS %s 103\n" % bundle_id

    def run(argv, **_kwargs):
        assert argv[0] == "scp"
        events.append("scp")
        return object()

    monkeypatch.setattr(publisher, "remote_python", remote_python)
    monkeypatch.setattr(publisher, "remote_verify", remote_verify)
    monkeypatch.setattr(publisher, "run", run)
    result = publisher.publish(JOB, "user@host", parent)
    assert result["status"] == "PASS"
    assert result["idempotent_no_write"] is False
    assert events == [
        "probe",
        "prepare",
        "scp",
        "verify_stage",
        "publish",
        "verify_final",
    ]
