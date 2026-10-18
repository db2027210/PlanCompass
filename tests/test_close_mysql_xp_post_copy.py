from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from shell.close_mysql_xp_post_copy import (
    AUTHORITATIVE_MANIFEST_SHA256,
    CommandResult,
    XPPostCopyError,
    close_post_copy,
)


class FakeRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def run(self, args, *, input_bytes=None):
        self.calls.append((tuple(args), input_bytes))
        if not self.results:
            raise AssertionError("unexpected command")
        return self.results.pop(0)


def _source(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    (source / "datadir").mkdir(parents=True)
    # Tests override the production digest constant through content preimage is
    # impossible, so patch the module constant to this fixture's digest.
    manifest = source / "database_manifest.json"
    manifest.write_text('{"status":"complete"}\n', encoding="utf-8")
    verifier = tmp_path / "verify.py"
    verifier.write_text("print('{}')\n", encoding="utf-8")
    return source, verifier


def _invoke(monkeypatch, tmp_path: Path, runner: FakeRunner, **kwargs):
    source, verifier = _source(tmp_path)
    digest = hashlib.sha256((source / "database_manifest.json").read_bytes()).hexdigest()
    monkeypatch.setattr(
        "shell.close_mysql_xp_post_copy.AUTHORITATIVE_MANIFEST_SHA256", digest
    )
    return close_post_copy(
        source=source,
        host="user@host02",
        target=Path("/safe/xp/template_v2"),
        rsync_pid=83927,
        runner=runner,
        verifier_path=verifier,
        pid_is_live=kwargs.pop("pid_is_live", lambda pid: False),
        **kwargs,
    )


def test_live_original_rsync_fails_before_any_command(monkeypatch, tmp_path: Path) -> None:
    runner = FakeRunner([])
    with pytest.raises(XPPostCopyError, match="still live"):
        _invoke(
            monkeypatch, tmp_path, runner, execute=False,
            pid_is_live=lambda pid: True,
        )
    assert runner.calls == []


@pytest.mark.parametrize(
    "result,match",
    [
        (CommandResult(23, "", "failure"), "rc=23"),
        (CommandResult(0, ">fcs.......|transfer.ibd\n", ""), "1 differences"),
    ],
)
def test_checksum_completion_probe_fails_closed(
    monkeypatch, tmp_path: Path, result: CommandResult, match: str,
) -> None:
    with pytest.raises(XPPostCopyError, match=match):
        _invoke(monkeypatch, tmp_path, FakeRunner([result]), execute=False)


def test_default_dry_run_only_executes_read_only_checksum_probe(
    monkeypatch, tmp_path: Path,
) -> None:
    runner = FakeRunner([CommandResult(0, "", "")])
    result = _invoke(monkeypatch, tmp_path, runner, execute=False)
    assert result["mode"] == "dry_run"
    assert result["mutations_performed"] is False
    assert len(runner.calls) == 1
    command = runner.calls[0][0]
    assert command[:5] == (
        "rsync", "-a", "--dry-run", "--checksum", "--itemize-changes"
    )
    assert runner.calls[0][1] is None


def test_execute_requires_three_literal_confirmations(
    monkeypatch, tmp_path: Path,
) -> None:
    runner = FakeRunner([CommandResult(0, "", "")])
    with pytest.raises(XPPostCopyError, match="exact"):
        _invoke(monkeypatch, tmp_path, runner, execute=True)
    assert len(runner.calls) == 1


def test_execute_publishes_seals_verifies_and_saves_hashed_json(
    monkeypatch, tmp_path: Path,
) -> None:
    verification = json.dumps({"status": "PASS", "read_only": True}) + "\n"
    runner = FakeRunner([
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(0, verification, ""),
    ])
    source, verifier = _source(tmp_path)
    digest = hashlib.sha256((source / "database_manifest.json").read_bytes()).hexdigest()
    monkeypatch.setattr(
        "shell.close_mysql_xp_post_copy.AUTHORITATIVE_MANIFEST_SHA256", digest
    )
    evidence = tmp_path / "evidence"
    result = close_post_copy(
        source=source,
        host="user@host02",
        target=Path("/safe/xp/template_v2"),
        rsync_pid=83927,
        execute=True,
        confirm_host="user@host02",
        confirm_source=str(source.absolute()),
        confirm_target="/safe/xp/template_v2",
        evidence_dir=evidence,
        runner=runner,
        verifier_path=verifier,
        pid_is_live=lambda pid: False,
    )
    assert result["remote_verification_status"] == "PASS"
    assert len(runner.calls) == 5
    assert runner.calls[1][1] == (source / "database_manifest.json").read_bytes()
    expected_sidecar = f"{digest}  database_manifest.json\n".encode("ascii")
    assert runner.calls[2][1] == expected_sidecar
    assert "find \"$root\" -xdev -type f -exec chmod 0444" in runner.calls[3][0][-1]
    assert runner.calls[4][1] == verifier.read_bytes()
    saved = evidence / "xp_post_copy_verification.json"
    assert saved.read_text() == verification
    saved_digest = hashlib.sha256(verification.encode()).hexdigest()
    assert (evidence / "xp_post_copy_verification.json.sha256").read_text() == (
        f"{saved_digest}  xp_post_copy_verification.json\n"
    )


def test_remote_verifier_failure_never_publishes_local_evidence(
    monkeypatch, tmp_path: Path,
) -> None:
    runner = FakeRunner([
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(1, '{"status":"FAIL"}\n', ""),
    ])
    source, verifier = _source(tmp_path)
    digest = hashlib.sha256((source / "database_manifest.json").read_bytes()).hexdigest()
    monkeypatch.setattr(
        "shell.close_mysql_xp_post_copy.AUTHORITATIVE_MANIFEST_SHA256", digest
    )
    evidence = tmp_path / "evidence"
    with pytest.raises(XPPostCopyError, match="remote verifier failed"):
        close_post_copy(
            source=source,
            host="user@host02",
            target=Path("/safe/xp/template_v2"),
            rsync_pid=83927,
            execute=True,
            confirm_host="user@host02",
            confirm_source=str(source.absolute()),
            confirm_target="/safe/xp/template_v2",
            evidence_dir=evidence,
            runner=runner,
            verifier_path=verifier,
            pid_is_live=lambda pid: False,
        )
    assert not evidence.exists()
