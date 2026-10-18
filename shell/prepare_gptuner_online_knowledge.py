#!/usr/bin/env python3
"""Build an audited, scene-specific GPTuner knowledge snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from google import genai
from google.genai import errors as gemini_errors
from google.genai import types as gemini_types
from openai import APIError, OpenAI, RateLimitError


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
PAPER_MODEL = "gpt-4"
STAGES = {
    "llm_source_extraction": True,
    "manual_source_summarization": True,
    "official_constraint_filter": True,
    "cross_source_contradiction_resolution": True,
    "default_range_debias": True,
    "factual_consistency_check": True,
    "prompt_ensemble_votes": 5,
    "hardware_aware_transformation": True,
    "special_value_classification": True,
}


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strip_html(value: str) -> str:
    return re.sub(r"<.*?>", "", value, flags=re.DOTALL).strip()


def parse_json_object(text: str) -> Dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(candidate[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM response is not a JSON object")
    return value


def cpuset_count(value: str) -> int:
    count = 0
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            low_text, high_text = token.split("-", 1)
            count += int(high_text) - int(low_text) + 1
        else:
            count += 1
    return count


def parse_memory_gib(value: Any) -> float:
    text = str(value or "").strip().lower()
    match = re.fullmatch(r"([0-9.]+)\s*([kmgt]?)b?", text)
    if not match:
        raise ValueError(f"cannot parse Docker memory limit: {value!r}")
    amount = float(match.group(1))
    factors = {"": 1.0 / (1024 ** 3), "k": 1.0 / (1024 ** 2), "m": 1.0 / 1024, "g": 1.0, "t": 1024.0}
    return amount * factors[match.group(2)]


def scene_hardware(config: Mapping[str, Any]) -> Dict[str, Any]:
    validation = ((config.get("binary_plan_tuning") or {}).get("validation") or {})
    workers = ((validation.get("docker_workers") or {}).get("workers") or [])
    if len(workers) != 2:
        raise RuntimeError("expected exactly two publication workers in config")
    cpu_counts = [cpuset_count(str(worker.get("cpuset_cpus") or "")) for worker in workers]
    memory_limits = [parse_memory_gib(worker.get("memory")) for worker in workers]
    if len(set(cpu_counts)) != 1 or len({round(value, 6) for value in memory_limits}) != 1:
        raise RuntimeError("publication workers have different hardware limits")
    data_dir = Path(str(workers[0].get("data_dir") or "/"))
    mount = Path("/disk1") if str(data_dir).startswith("/disk1/") else data_dir.anchor
    stat = os.statvfs(str(mount))
    disk_gib = stat.f_blocks * stat.f_frsize / (1024 ** 3)
    return {
        "cpu_logical_cores": cpu_counts[0],
        "memory_gib": memory_limits[0],
        "disk_gib": round(disk_gib, 3),
        "disk_type": "NVMe SSD" if str(mount) in {"/disk1", "/disk2"} else "SSD",
        "worker_count": len(workers),
        "worker_containers": [str(worker.get("container") or "") for worker in workers],
        "hardware_scope": "per_database_container_not_host",
    }


def mysql_scene_hardware(binding: Mapping[str, Any], scene: str) -> Dict[str, Any]:
    workers = [
        worker
        for worker in binding.get("workers") or []
        if str(worker.get("scene") or "") == scene
    ]
    if len(workers) != 2:
        raise RuntimeError(f"expected exactly two MySQL workers for {scene}")
    cpu_counts = [float(worker.get("cpu_quota_cores") or 0.0) for worker in workers]
    memory_limits = [float(worker.get("memory_bytes") or 0.0) / (1024 ** 3) for worker in workers]
    if len({round(value, 6) for value in cpu_counts}) != 1 or len(
        {round(value, 6) for value in memory_limits}
    ) != 1:
        raise RuntimeError("publication MySQL workers have different hardware limits")
    data_dir = Path(str(workers[0].get("datadir") or "/"))
    mount = Path("/disk1") if str(data_dir).startswith("/disk1/") else data_dir.anchor
    stat = os.statvfs(str(mount))
    return {
        "cpu_logical_cores": cpu_counts[0],
        "memory_gib": memory_limits[0],
        "disk_gib": round(stat.f_blocks * stat.f_frsize / (1024 ** 3), 3),
        "disk_type": "NVMe SSD" if str(mount) in {"/disk1", "/disk2"} else "SSD",
        "worker_count": len(workers),
        "worker_containers": [str(worker.get("container") or "") for worker in workers],
        "hardware_scope": "per_database_container_not_host",
    }


def official_description(document: Mapping[str, Any], knob: str) -> str:
    for item in document.get("params") or []:
        if item.get("name") == knob:
            return strip_html(str(item.get("description") or ""))
    return ""


class AuditedLLM:
    """Common accounting surface for audited LLM transports."""

    def __init__(self, model: str, provider: str, log_path: Path) -> None:
        self.model = model
        self.provider = provider
        self.log_path = log_path
        self.call_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0


class AuditedOpenAI(AuditedLLM):
    """Audited OpenAI-compatible transport that records every prompt and response."""

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str],
        model: str,
        log_path: Path,
        provider_label: Optional[str] = None,
    ) -> None:
        provider = provider_label or (
            "openai-compatible-api" if base_url else "openai-api"
        )
        super().__init__(model, provider, log_path)
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)

    def call(self, stage: str, knob: str, prompt: str, *, json_output: bool) -> Any:
        messages = []
        if json_output:
            messages.append(
                {"role": "system", "content": "Return only one valid JSON object."}
            )
        messages.append({"role": "user", "content": prompt})
        last_error: Optional[Exception] = None
        for attempt in range(1, 6):
            started = time.time()
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.5 if json_output else 1.0,
                )
                text = str(response.choices[0].message.content or "").strip()
                value = parse_json_object(text) if json_output else text
                usage = response.usage
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                self.call_count += 1
                self.prompt_tokens += prompt_tokens
                self.completion_tokens += completion_tokens
                self.total_tokens += prompt_tokens + completion_tokens
                append_jsonl(
                    self.log_path,
                    {
                        "attempt": attempt,
                        "completion_tokens": completion_tokens,
                        "elapsed_s": time.time() - started,
                        "knob": knob,
                        "model": self.model,
                        "provider": self.provider,
                        "prompt": prompt,
                        "prompt_tokens": prompt_tokens,
                        "response": text,
                        "response_id": response.id,
                        "stage": stage,
                        "status": "ok",
                    },
                )
                return value
            except (APIError, RateLimitError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                append_jsonl(
                    self.log_path,
                    {
                        "attempt": attempt,
                        "elapsed_s": time.time() - started,
                        "error": f"{type(exc).__name__}: {exc}",
                        "knob": knob,
                        "model": self.model,
                        "provider": self.provider,
                        "stage": stage,
                        "status": "retry" if attempt < 5 else "failed",
                    },
                )
                if attempt < 5:
                    time.sleep(min(30.0, 2.0 ** attempt))
        raise RuntimeError(f"LLM stage failed: {stage}/{knob}: {last_error}")


class AuditedGemini(AuditedLLM):
    """Official Google Gen AI SDK transport with complete call accounting."""

    def __init__(self, api_key: str, model: str, log_path: Path) -> None:
        super().__init__(model, "google-gemini-api", log_path)
        self.client = genai.Client(
            api_key=api_key,
            http_options=gemini_types.HttpOptions(api_version="v1beta"),
        )

    def call(self, stage: str, knob: str, prompt: str, *, json_output: bool) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(1, 6):
            started = time.time()
            try:
                config = gemini_types.GenerateContentConfig(
                    temperature=0.5 if json_output else 1.0,
                    response_mime_type=(
                        "application/json" if json_output else "text/plain"
                    ),
                )
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )
                text = str(response.text or "").strip()
                value = parse_json_object(text) if json_output else text
                usage = response.usage_metadata
                prompt_tokens = int(
                    getattr(usage, "prompt_token_count", 0) or 0
                )
                completion_tokens = int(
                    getattr(usage, "candidates_token_count", 0) or 0
                )
                total_tokens = int(
                    getattr(usage, "total_token_count", 0)
                    or prompt_tokens + completion_tokens
                )
                self.call_count += 1
                self.prompt_tokens += prompt_tokens
                self.completion_tokens += completion_tokens
                self.total_tokens += total_tokens
                append_jsonl(
                    self.log_path,
                    {
                        "attempt": attempt,
                        "completion_tokens": completion_tokens,
                        "elapsed_s": time.time() - started,
                        "knob": knob,
                        "model": self.model,
                        "provider": self.provider,
                        "prompt": prompt,
                        "prompt_tokens": prompt_tokens,
                        "response": text,
                        "response_id": getattr(response, "response_id", None),
                        "stage": stage,
                        "status": "ok",
                        "total_tokens": total_tokens,
                    },
                )
                return value
            except (
                gemini_errors.APIError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                last_error = exc
                append_jsonl(
                    self.log_path,
                    {
                        "attempt": attempt,
                        "elapsed_s": time.time() - started,
                        "error": f"{type(exc).__name__}: {exc}",
                        "knob": knob,
                        "model": self.model,
                        "provider": self.provider,
                        "stage": stage,
                        "status": "retry" if attempt < 5 else "failed",
                    },
                )
                if attempt < 5:
                    time.sleep(min(30.0, 2.0 ** attempt))
        raise RuntimeError(f"Gemini LLM stage failed: {stage}/{knob}: {last_error}")


class AuditedCodex(AuditedLLM):
    """Current-account Codex CLI transport for an explicitly labelled variant."""

    def __init__(
        self,
        model: str,
        reasoning_effort: str,
        codex_home: Optional[Path],
        log_path: Path,
    ) -> None:
        super().__init__(model, "codex-chatgpt-account", log_path)
        self.reasoning_effort = reasoning_effort
        self.codex_home = codex_home

    def call(self, stage: str, knob: str, prompt: str, *, json_output: bool) -> Any:
        instruction = (
            "Return only one valid JSON object. Do not use tools or read files.\n\n"
            if json_output
            else "Answer directly. Do not use tools or read files.\n\n"
        )
        full_prompt = instruction + prompt
        last_error: Optional[Exception] = None
        for attempt in range(1, 6):
            started = time.time()
            try:
                with tempfile.TemporaryDirectory(prefix="gptuner_codex_") as temporary:
                    output_path = Path(temporary) / "last_message.txt"
                    command = [
                        "codex",
                        "exec",
                        "--ephemeral",
                        "--ignore-user-config",
                        "--ignore-rules",
                        "--skip-git-repo-check",
                        "-s",
                        "read-only",
                        "-c",
                        f'model_reasoning_effort="{self.reasoning_effort}"',
                        "-m",
                        self.model,
                        "-o",
                        str(output_path),
                        "-",
                    ]
                    completed = subprocess.run(
                        command,
                        input=full_prompt,
                        cwd="/tmp",
                        capture_output=True,
                        text=True,
                        timeout=300,
                        check=False,
                        env=self._subprocess_env(),
                    )
                    if completed.returncode != 0:
                        detail = (completed.stderr or completed.stdout).strip()[-2000:]
                        raise RuntimeError(
                            f"codex exited {completed.returncode}: {detail}"
                        )
                    text = output_path.read_text(encoding="utf-8").strip()
                    usage_text = completed.stdout + "\n" + completed.stderr
                    usage_match = re.search(r"tokens used\s+([0-9,]+)", usage_text)
                    total_tokens = (
                        int(usage_match.group(1).replace(",", ""))
                        if usage_match is not None
                        else 0
                    )
                value = parse_json_object(text) if json_output else text
                self.call_count += 1
                self.total_tokens += total_tokens
                append_jsonl(
                    self.log_path,
                    {
                        "attempt": attempt,
                        "elapsed_s": time.time() - started,
                        "knob": knob,
                        "model": self.model,
                        "provider": self.provider,
                        "prompt": prompt,
                        "reasoning_effort": self.reasoning_effort,
                        "response": text,
                        "stage": stage,
                        "status": "ok",
                        "total_tokens_reported_by_codex": total_tokens,
                    },
                )
                return value
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                append_jsonl(
                    self.log_path,
                    {
                        "attempt": attempt,
                        "elapsed_s": time.time() - started,
                        "error": f"{type(exc).__name__}: {exc}",
                        "knob": knob,
                        "model": self.model,
                        "provider": self.provider,
                        "stage": stage,
                        "status": "retry" if attempt < 5 else "failed",
                    },
                )
                if attempt < 5:
                    time.sleep(min(30.0, 2.0 ** attempt))
        raise RuntimeError(f"Codex LLM stage failed: {stage}/{knob}: {last_error}")

    def _subprocess_env(self) -> Dict[str, str]:
        environment = dict(os.environ)
        if self.codex_home is not None:
            environment["CODEX_HOME"] = str(self.codex_home)
        return environment


def majority(values: Sequence[Any]) -> Any:
    normalized = [json.dumps(value, sort_keys=True) for value in values]
    winner = Counter(normalized).most_common(1)[0][0]
    return json.loads(winner)


def prepare_knob(
    llm: AuditedLLM,
    official_root: Path,
    output_dir: Path,
    knob: str,
    system_view: Mapping[str, Any],
    official_document: Mapping[str, Any],
    hardware: Mapping[str, Any],
    workload: Mapping[str, Any],
    rng: random.Random,
    dbms: str,
    official_sources: Mapping[str, Any],
) -> Dict[str, Any]:
    source_root = official_root / f"knowledge_collection/{dbms}/knowledge_sources"
    if dbms == "mysql":
        description = str(
            ((official_sources.get("parameters") or {}).get(knob) or {}).get(
                "description"
            )
            or ""
        )
    else:
        description = official_description(official_document, knob)
    dbms_label = "MySQL" if dbms == "mysql" else "PostgreSQL"
    gpt_suggestion = llm.call(
        "llm_source_extraction",
        knob,
        f"You are an experienced {dbms_label} DBA. Summarize how to tune '{knob}' in one actionable sentence. Include concrete recommended values or ranges only when justified.",
        json_output=False,
    )
    manual_suggestion = llm.call(
        "manual_source_summarization",
        knob,
        f"Summarize this official {dbms_label} description for '{knob}' in one actionable tuning sentence. Preserve justified numbers.\nDESCRIPTION:\n{description}",
        json_output=False,
    ) if description else None
    web_path = source_root / "web" / f"{knob}.txt"
    web_suggestion = web_path.read_text(encoding="utf-8").strip() if web_path.is_file() else None
    constrained = llm.call(
        "official_constraint_filter",
        knob,
        "Remove only claims or numerical values that contradict the official system view. Return keys gpt_suggestion and web_suggestion.\n"
        f"SYSTEM_VIEW:\n{json.dumps(system_view[knob], ensure_ascii=False)}\n"
        f"GPT_SUGGESTION:\n{gpt_suggestion}\nWEB_SUGGESTION:\n{web_suggestion}",
        json_output=True,
    )
    sources = {
        "manual_suggestion": manual_suggestion,
        "web_suggestion": constrained.get("web_suggestion"),
        "gpt_suggestion": constrained.get("gpt_suggestion"),
    }
    resolved = llm.call(
        "cross_source_contradiction_resolution",
        knob,
        "Resolve contradictions while preserving non-conflicting information. Priority is manual, then web, then GPT. Return the same three keys.\n"
        f"SOURCES:\n{json.dumps(sources, ensure_ascii=False)}",
        json_output=True,
    )
    debiased = llm.call(
        "default_range_debias",
        knob,
        "Remove statements that merely repeat the DBMS legal min/max as if they were performance recommendations. Preserve genuine recommended values and ranges. Return the same three source keys.\n"
        f"SYSTEM_VIEW:\n{json.dumps(system_view[knob], ensure_ascii=False)}\n"
        f"SOURCES:\n{json.dumps(resolved, ensure_ascii=False)}",
        json_output=True,
    )
    summary = llm.call(
        "cross_source_summary",
        knob,
        f"Produce one concise, actionable {dbms_label} tuning suggestion supported only by these sources. Do not repeat the DBMS legal min/max merely as advice.\n"
        f"SOURCES:\n{json.dumps(debiased, ensure_ascii=False)}",
        json_output=False,
    )
    consistency_passed = False
    for round_index in range(1, 4):
        check = llm.call(
            "factual_consistency_check",
            knob,
            "Determine whether every claim in SUMMARY is supported by SOURCES. Return {\"consistent\": true|false, \"unsupported_claims\": []}.\n"
            f"SOURCES:\n{json.dumps(debiased, ensure_ascii=False)}\nSUMMARY:\n{summary}",
            json_output=True,
        )
        if check.get("consistent") is True:
            consistency_passed = True
            break
        summary = llm.call(
            "factual_consistency_revision",
            knob,
            "Rewrite SUMMARY so every claim is supported by SOURCES, removing the listed unsupported claims. Return only the revised suggestion.\n"
            f"SOURCES:\n{json.dumps(debiased, ensure_ascii=False)}\nSUMMARY:\n{summary}\nCHECK:\n{json.dumps(check, ensure_ascii=False)}",
            json_output=False,
        )
    if not consistency_passed:
        raise RuntimeError(f"LLM factual consistency did not pass for {knob}")
    tuning_path = output_dir / "tuning_lake" / f"{knob}.txt"
    tuning_path.parent.mkdir(parents=True, exist_ok=True)
    tuning_path.write_text(summary + "\n", encoding="utf-8")

    examples = list((official_root / "example_pool").glob("*.txt"))
    votes: List[Dict[str, Any]] = []
    for vote_index in range(5):
        chosen = rng.sample(examples, k=min(3, len(examples)))
        examples_text = "\n".join(path.read_text(encoding="utf-8") for path in chosen)
        vote = llm.call(
            "prompt_ensemble_vote",
            knob,
            "Given the suggestion and per-container hardware, extract discrete suggested_values and a recommended min_value/max_value interval. Values for this planner cost knob are unitless numbers. Legal DBMS limits are constraints, not recommendations. Return exactly suggested_values (array), min_value, max_value.\n"
            f"KNOB: {knob}\nSUGGESTION: {summary}\nHARDWARE: {json.dumps(hardware)}\nWORKLOAD: {json.dumps(workload)}\nFEW_SHOT_EXAMPLES:\n{examples_text}",
            json_output=True,
        )
        votes.append(vote)
    structured = {
        "suggested_values": majority([vote.get("suggested_values") or [] for vote in votes]),
        "min_value": majority([vote.get("min_value") for vote in votes]),
        "max_value": majority([vote.get("max_value") for vote in votes]),
        "hardware": dict(hardware),
        "workload": dict(workload),
        "prompt_ensemble_votes": 5,
    }
    for key in ("min_value", "max_value"):
        value = structured[key]
        if value is not None:
            numeric = float(value)
            if not math.isfinite(numeric):
                raise RuntimeError(f"non-finite {key} for {knob}")
            structured[key] = numeric
    structured["suggested_values"] = [float(value) for value in structured["suggested_values"]]
    write_json(output_dir / "structured_knowledge/normal" / f"{knob}.json", structured)
    special = llm.call(
        "special_value_classification",
        knob,
        f"Classify whether this numerical {dbms_label} knob has a special sentinel value whose semantics differ from its ordered numeric behavior. Return {{\"special_knob\": true|false, \"special_value\": number|null}}.\n"
        f"KNOB: {knob}\nSYSTEM_VIEW: {json.dumps(system_view[knob])}\nDESCRIPTION: {description}",
        json_output=True,
    )
    write_json(output_dir / "structured_knowledge/special" / f"{knob}.json", special)
    write_json(
        output_dir / "sources" / f"{knob}.json",
        {
            "gpt_suggestion": gpt_suggestion,
            "manual_suggestion": manual_suggestion,
            "web_suggestion": web_suggestion,
            "resolved_sources": resolved,
            "default_range_debiased_sources": debiased,
            "summary": summary,
            "web_source_available": web_path.is_file(),
        },
    )
    return structured


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", required=True)
    parser.add_argument("--dbms", choices=("postgres", "mysql"), default="postgres")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--worker-binding", type=Path)
    parser.add_argument("--parameter-table", type=Path)
    parser.add_argument(
        "--official-sources",
        type=Path,
        default=ROOT / "config/formal/mysql_optimizer_cost_official_sources_v1.json",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--official-root", type=Path, default=ROOT / "baselines/GPTuner_official")
    parser.add_argument("--model", default=PAPER_MODEL)
    parser.add_argument(
        "--provider",
        choices=("openai-api", "deepseek-api", "gemini-api", "codex-cli"),
        default="openai-api",
    )
    parser.add_argument(
        "--allow-model-substitution",
        action="store_true",
        help="Allow a clearly labelled non-paper-model knowledge variant.",
    )
    parser.add_argument(
        "--codex-reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
        default="low",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=None,
        help="Optional isolated CODEX_HOME already logged into an authorized account.",
    )
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--seed", type=int, default=20260904)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paper_model_equivalent = (
        args.provider == "openai-api"
        and args.model == PAPER_MODEL
        and not args.base_url
    )
    if not paper_model_equivalent and not args.allow_model_substitution:
        raise ValueError(
            "non-paper LLM transport/model requires --allow-model-substitution"
        )
    output_dir = args.output_dir.resolve()
    if (output_dir / "manifest.json").exists():
        raise RuntimeError(f"refusing to overwrite completed snapshot: {output_dir}")
    official_root = args.official_root.resolve()
    official_sources: Dict[str, Any] = {}
    if args.dbms == "postgres":
        if args.config is None:
            raise ValueError("PostgreSQL knowledge preparation requires --config")
        config = read_json(args.config.resolve())
        hardware = scene_hardware(config)
        active_params = list(ACTIVE_PARAMS)
        workload_name = str(
            ((config.get("binary_plan_tuning") or {}).get("workload_name"))
            or ((config.get("algorithm_c") or {}).get("benchmark"))
            or args.scene
        )
        system_view = read_json(
            official_root / "knowledge_collection/postgres/knob_info/system_view.json"
        )
        official_document = read_json(
            official_root / "knowledge_collection/postgres/knob_info/official_document.json"
        )
        input_path = args.config.resolve()
    else:
        if args.worker_binding is None or args.parameter_table is None:
            raise ValueError(
                "MySQL knowledge preparation requires --worker-binding and --parameter-table"
            )
        binding = read_json(args.worker_binding.resolve())
        table = read_json(args.parameter_table.resolve())
        official_sources = read_json(args.official_sources.resolve())
        hardware = mysql_scene_hardware(binding, args.scene)
        space = table.get("parameter_space") or {}
        active_params = [str(name) for name in space.get("active_params") or []]
        if len(active_params) != 4:
            raise RuntimeError("MySQL publication parameter table must have four knobs")
        source_params = official_sources.get("parameters") or {}
        if any(name not in source_params for name in active_params):
            raise RuntimeError("MySQL official source map is incomplete")
        system_view = {
            name: {
                "name": name,
                "vartype": "float",
                "min_val": float(space["ranges"][name]["lower"]),
                "max_val": float(space["ranges"][name]["upper"]),
                "default": float(source_params[name]["default"]),
                "official_manual_url": official_sources["manual_url"],
                "official_source_code_url": official_sources["source_code_url"],
            }
            for name in active_params
        }
        official_document = {}
        workload_name = args.scene
        input_path = args.parameter_table.resolve()
    workload = {
        "scene": args.scene,
        "workload": workload_name,
        "optimization_objective": "same_worker_normalized_db_qps",
    }
    if args.provider in {"openai-api", "deepseek-api"}:
        api_key = os.environ.get(args.api_key_env)
        if not api_key:
            raise RuntimeError(f"missing API credential in {args.api_key_env}")
        base_url = args.base_url
        provider_label: Optional[str] = None
        if args.provider == "deepseek-api":
            base_url = base_url or "https://api.deepseek.com"
            if base_url.rstrip("/") not in {
                "https://api.deepseek.com",
                "https://api.deepseek.com/v1",
            }:
                raise ValueError(
                    "deepseek-api requires the official https://api.deepseek.com endpoint"
                )
            provider_label = "deepseek-api"
        llm: AuditedLLM = AuditedOpenAI(
            api_key,
            base_url,
            args.model,
            output_dir / "llm_calls.jsonl",
            provider_label=provider_label,
        )
    elif args.provider == "gemini-api":
        if args.base_url:
            raise ValueError("--base-url is not valid for the Gemini API provider")
        api_key = os.environ.get(args.api_key_env)
        if not api_key:
            raise RuntimeError(f"missing API credential in {args.api_key_env}")
        llm = AuditedGemini(
            api_key,
            args.model,
            output_dir / "llm_calls.jsonl",
        )
    else:
        if args.base_url:
            raise ValueError("--base-url is not valid for the Codex CLI provider")
        llm = AuditedCodex(
            args.model,
            args.codex_reasoning_effort,
            args.codex_home.resolve() if args.codex_home is not None else None,
            output_dir / "llm_calls.jsonl",
        )
    rng = random.Random(args.seed)
    started = time.time()
    for knob in active_params:
        prepare_knob(
            llm,
            official_root,
            output_dir,
            knob,
            system_view,
            official_document,
            hardware,
            workload,
            rng,
            args.dbms,
            official_sources,
        )
    artifacts = sorted(
        path for path in output_dir.rglob("*") if path.is_file() and path.name != "manifest.json"
    )
    manifest = {
        "active_params": list(active_params),
        "artifact_sha256": {str(path.relative_to(output_dir)): sha256_file(path) for path in artifacts},
        "base_url_configured": bool(args.base_url or args.provider == "deepseek-api"),
        "completion_tokens": llm.completion_tokens,
        "codex_home_isolated": args.codex_home is not None,
        "config_path": str(input_path),
        "config_sha256": sha256_file(input_path),
        "contract": "gptuner_online_knowledge_snapshot_v1",
        "dbms": args.dbms,
        "elapsed_s": time.time() - started,
        "hardware": hardware,
        "llm_call_count": llm.call_count,
        "model": args.model,
        "paper_model": PAPER_MODEL,
        "paper_model_equivalent": paper_model_equivalent,
        "provider": llm.provider,
        "official_commit": subprocess.run(
            ["git", "-C", str(official_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "official_source_manifest": (
            {
                "path": str(args.official_sources.resolve()),
                "sha256": sha256_file(args.official_sources.resolve()),
                "manual_url": official_sources.get("manual_url"),
                "source_code_url": official_sources.get("source_code_url"),
            }
            if args.dbms == "mysql"
            else None
        ),
        "prompt_tokens": llm.prompt_tokens,
        "total_tokens": llm.total_tokens,
        "scene": args.scene,
        "seed": args.seed,
        "stages": STAGES,
        "status": "complete",
        "workload": workload,
    }
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
