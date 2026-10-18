#!/usr/bin/env python3
"""Offline transport/provenance tests for GPTuner knowledge preparation."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from prepare_gptuner_online_knowledge import AuditedCodex, AuditedOpenAI


ROOT = Path(__file__).resolve().parents[1]


def test_deepseek_transport_uses_official_endpoint_and_provider_label() -> None:
    with tempfile.TemporaryDirectory(prefix="test_gptuner_deepseek_") as temporary:
        log_path = Path(temporary) / "calls.jsonl"
        with mock.patch("prepare_gptuner_online_knowledge.OpenAI") as client:
            transport = AuditedOpenAI(
                "test-secret-not-a-real-key",
                "https://api.deepseek.com",
                "deepseek-v4-pro",
                log_path,
                provider_label="deepseek-api",
            )

        client.assert_called_once_with(
            api_key="CHANGE_ME",
            base_url="https://api.deepseek.com",
        )
        assert transport.model == "deepseek-v4-pro"
        assert transport.provider == "deepseek-api"


def test_deepseek_publication_wiring_is_explicit_and_symmetric() -> None:
    for relative in (
        "shell/run_pg_external_baselines_publication_v1.sh",
        "shell/run_mysql_external_baselines_publication_v1.sh",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "deepseek-api)" in source
        assert "DEEPSEEK_API_KEY" in source
        assert "--api-key-env DEEPSEEK_API_KEY" in source
        assert "--base-url" in source
        assert "https://api.deepseek.com" in source
        assert "gptuner_manifest_provider=deepseek-api" in source

    launcher = (
        ROOT / "shell/run_pg_mysql_external_baseline_deepseek_suite_v1.sh"
    ).read_text(encoding="utf-8")
    assert "GPTUNER_LLM_PROVIDER=deepseek-api" in launcher
    assert "GPTUNER_LLM_MODEL=deepseek-v4-pro" in launcher
    assert "GPTUNER_ALLOW_MODEL_SUBSTITUTION=1" in launcher
    assert "paper_model_equivalent=false" in launcher

    handoff = (
        ROOT / "shell/wait_aqetuner_then_run_gptuner_deepseek_single_v1.sh"
    ).read_text(encoding="utf-8")
    assert "aqetuner_single_campaign_authority.json" in handoff
    assert 'and .status == "complete"' in handoff
    assert "MYSQL_EXTERNAL_REUSE_PREPARED_WORKERS=1" in handoff
    assert "run_pg_mysql_external_baseline_deepseek_suite_v1.sh" in handoff

    suite = (
        ROOT / "shell/run_pg_mysql_external_baseline_publication_suite_v1.sh"
    ).read_text(encoding="utf-8")
    assert "resolve_pg_seed_base" in suite
    assert "partial PostgreSQL AQETuner authority set" in suite
    assert "observed_seed=$(jq -er '.seed'" in suite
    assert "authority_seed_base=$((first_seed - 1))" in suite
    assert "explicit PostgreSQL seed base differs from completed AQETuner" in suite


def test_codex_transport_records_model_provider_and_usage() -> None:
    with tempfile.TemporaryDirectory(prefix="test_gptuner_codex_") as temporary:
        root = Path(temporary)
        log_path = root / "calls.jsonl"
        isolated_home = root / "isolated_codex"
        isolated_home.mkdir()
        transport = AuditedCodex(
            "gpt-5.4-mini",
            "low",
            isolated_home,
            log_path,
        )

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_text('{"consistent": true}\n', encoding="utf-8")
            environment = kwargs.get("env")
            assert isinstance(environment, dict)
            assert environment["CODEX_HOME"] == str(isolated_home)
            assert kwargs["input"]
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="tokens used\n1,234\n",
                stderr="",
            )

        with mock.patch("prepare_gptuner_online_knowledge.subprocess.run", fake_run):
            result = transport.call(
                "factual_consistency_check",
                "random_page_cost",
                "Check this summary.",
                json_output=True,
            )

        assert result == {"consistent": True}
        assert transport.call_count == 1
        assert transport.total_tokens == 1234
        row = json.loads(log_path.read_text(encoding="utf-8"))
        assert row["model"] == "gpt-5.4-mini"
        assert row["provider"] == "codex-chatgpt-account"
        assert row["reasoning_effort"] == "low"
        assert row["total_tokens_reported_by_codex"] == 1234


if __name__ == "__main__":
    test_deepseek_transport_uses_official_endpoint_and_provider_label()
    test_deepseek_publication_wiring_is_explicit_and_symmetric()
    test_codex_transport_records_model_provider_and_usage()
    print("PASS: GPTuner audited Codex and DeepSeek transports", flush=True)
