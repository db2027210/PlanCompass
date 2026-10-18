#!/usr/bin/env python3
"""Print an authoritative one-shot progress table for Algorithm A."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple


SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
TERMINAL_FAILURE_MARKERS = (
    "technical_failure_stopped",
    "nonconvergence",
    "all_champions_semantic_stability_failed",
    "candidate_space_exhausted",
)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def last_jsonl_event(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    last = ""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                last = line
    if not last:
        return None
    try:
        value = json.loads(last)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def candidate_budget_exceeded(validation: Mapping[str, Any]) -> bool:
    """candidate budget exceeded."""

    completeness = validation.get("workload_completeness")
    completeness = completeness if isinstance(completeness, Mapping) else {}
    errors = {
        str(value)
        for value in (completeness.get("errors") or [])
    }
    process = validation.get("process")
    process = process if isinstance(process, Mapping) else {}
    resource_stats = validation.get("resource_stats")
    resource_stats = (
        resource_stats if isinstance(resource_stats, Mapping) else {}
    )
    target_wall_time_s = float(
        completeness.get("target_wall_time_s") or 0.0
    )
    explicit_wall_rejection = (
        completeness.get("completed_within_target_wall_time") is False
        or any(value.endswith("_target_wall_time_exceeded") for value in errors)
    )
    timed_out_after_budget = (
        process.get("timed_out") is True
        and float(process.get("timeout_s") or 0.0) > 0.0
    )
    technical_markers = {
        str(value)
        for value in (validation.get("workload_technical_failures") or [])
    }
    return bool(
        str(validation.get("status") or "").lower() != "ok"
        and (explicit_wall_rejection or timed_out_after_budget)
        and resource_stats.get("resource_monitor_ok") is True
        and not (validation.get("db_errors") or [])
        # The SSD qualification wrapper inherits this marker whenever the
        # candidate payload is invalid. It is a consequence of the timeout,
        # not an independent SSD/monitor infrastructure failure.
        and technical_markers.issubset({"ssd_qualification_out_of_band"})
    )


def exact_candidate_identity(params: Mapping[str, Any]) -> Optional[str]:
    """Match the formal controller's canonical exact-parameter signature."""

    if not all(name in params for name in ACTIVE_PARAMS):
        return None
    try:
        values = tuple(format(float(params[name]), ".17g") for name in ACTIVE_PARAMS)
    except (TypeError, ValueError, OverflowError):
        return None
    return "params:" + json.dumps(
        values,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def round_replay_counts(path: Path, round_index: int) -> Tuple[int, int, int, int]:
    if not path.is_file() or round_index <= 0:
        return 0, 0, 0, 0
    first_by_identity: Dict[str, Mapping[str, Any]] = {}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (
                not isinstance(row, dict)
                or row.get("event") != "algorithm_c_candidate_replay"
                or int(row.get("round_index") or 0) != round_index
                or row.get("batch_kind") != "algorithm_a_full_factorial"
            ):
                continue
            validation = row.get("validation")
            if isinstance(validation, Mapping):
                candidate = validation.get("candidate_ranking")
                candidate = candidate if isinstance(candidate, Mapping) else {}
                params = candidate.get("optimized_params")
                if isinstance(params, Mapping) and params:
                    identity = exact_candidate_identity(params)
                    if identity is None:
                        continue
                else:
                    # A row without a complete formal parameter vector proves
                    # a physical invocation, but it cannot prove coverage of a
                    # unique Algorithm A factorial candidate.
                    continue
                first_by_identity.setdefault(identity, validation)

    terminal_attempted = 0
    valid = 0
    technical = 0
    budget_exceeded = 0
    for validation in first_by_identity.values():
        resource_stats = validation.get("resource_stats")
        resource_stats = (
            resource_stats if isinstance(resource_stats, Mapping) else {}
        )
        completion = validation.get("completion_domain_accounting")
        completion = completion if isinstance(completion, Mapping) else {}
        excluded_from_valid = completion.get("excluded_from_valid_replays") is True
        excluded_from_lane_technical = (
            completion.get("excluded_from_lane_technical_failures") is True
        )
        status_ok = str(validation.get("status") or "").lower() == "ok"
        status_failed = str(validation.get("status") or "").lower() == "failed"
        monitor_ok = resource_stats.get("resource_monitor_ok") is True
        is_budget_exceeded = candidate_budget_exceeded(validation)
        is_technical = (
            status_failed
            or (
                bool(validation.get("technical_failure"))
                and not excluded_from_lane_technical
            )
        ) and not is_budget_exceeded
        is_valid = status_ok and monitor_ok and not excluded_from_valid
        if is_valid:
            valid += 1
        # Invalid invocations remain excluded from valid replay and acceptance,
        # but every real workload invocation consumes a terminal physical slot.
        if is_technical:
            technical += 1
        if is_budget_exceeded:
            budget_exceeded += 1
        # Match Algorithm A's authoritative resume contract: reserving a real
        # replay slot is terminal even when a technical failure occurs before
        # the workload process starts (for example while applying parameters).
        if validation.get("used_real_replay") is True:
            terminal_attempted += 1
    return terminal_attempted, valid, technical, budget_exceeded


class IncrementalHistoryCache:
    """IncrementalHistoryCache."""

    VERSION = 9

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: Dict[str, Any] = {"version": self.VERSION, "files": {}}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            value = {}
        if isinstance(value, dict) and value.get("version") == self.VERSION:
            files = value.get("files")
            if isinstance(files, dict):
                self.data = value

    @staticmethod
    def _validation_summary(validation: Mapping[str, Any]) -> Dict[str, Any]:
        resource_stats = validation.get("resource_stats")
        resource_stats = (
            resource_stats if isinstance(resource_stats, Mapping) else {}
        )
        completion = validation.get("completion_domain_accounting")
        completion = completion if isinstance(completion, Mapping) else {}
        status = str(validation.get("status") or "").lower()
        status_ok = status == "ok"
        status_failed = status == "failed"
        excluded_from_valid = (
            completion.get("excluded_from_valid_replays") is True
        )
        monitor_ok = resource_stats.get("resource_monitor_ok") is True
        budget_exceeded = candidate_budget_exceeded(validation)
        return {
            "status_ok": status_ok,
            "status_failed": status_failed,
            "monitor_ok": monitor_ok,
            "terminal_attempt": bool(validation.get("used_real_replay") is True),
            "budget_exceeded": budget_exceeded,
            "technical": (
                status_failed
                or (
                    bool(validation.get("technical_failure"))
                    and completion.get("excluded_from_lane_technical_failures")
                    is not True
                )
            )
            and not budget_exceeded,
            "excluded_from_valid": excluded_from_valid,
        }

    @staticmethod
    def _candidate_identity(
        row: Mapping[str, Any], validation: Mapping[str, Any]
    ) -> str:
        candidate = validation.get("candidate_ranking")
        candidate = candidate if isinstance(candidate, Mapping) else {}
        params = candidate.get("optimized_params")
        if isinstance(params, Mapping) and params:
            identity = exact_candidate_identity(params)
            if identity is not None:
                return identity
        return ""

    def update(self, history_path: Path) -> None:
        if not history_path.is_file():
            return
        stat = history_path.stat()
        key = str(history_path.resolve())
        files = self.data.setdefault("files", {})
        entry = files.get(key)
        if not isinstance(entry, dict):
            entry = {}
        inode = int(entry.get("inode") or -1)
        offset = int(entry.get("offset") or 0)
        if inode != int(stat.st_ino) or stat.st_size < offset:
            entry = {"inode": int(stat.st_ino), "offset": 0, "rounds": {}}
            offset = 0

        with history_path.open("rb") as handle:
            handle.seek(offset)
            appended = handle.read()
        newline = appended.rfind(b"\n")
        if newline < 0:
            files[key] = entry
            return

        rounds = entry.setdefault("rounds", {})
        for raw_line in appended[: newline + 1].splitlines():
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except (UnicodeDecodeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            entry["last_event"] = {
                "event": row.get("event"),
                "time": row.get("time"),
            }
            if (
                row.get("event") != "algorithm_c_candidate_replay"
                or row.get("batch_kind") != "algorithm_a_full_factorial"
            ):
                continue
            round_index = int(row.get("round_index") or 0)
            validation = row.get("validation")
            if round_index <= 0 or not isinstance(validation, Mapping):
                continue
            round_rows = rounds.setdefault(str(round_index), {})
            identity = self._candidate_identity(row, validation)
            if not identity:
                continue
            round_rows.setdefault(
                identity,
                self._validation_summary(validation),
            )

        entry["inode"] = int(stat.st_ino)
        entry["offset"] = offset + newline + 1
        files[key] = entry

    def last_event(self, history_path: Path) -> Optional[Dict[str, Any]]:
        self.update(history_path)
        entry = self.data.get("files", {}).get(str(history_path.resolve()), {})
        value = entry.get("last_event") if isinstance(entry, Mapping) else None
        return dict(value) if isinstance(value, Mapping) else None

    def round_counts(
        self, history_path: Path, round_index: int
    ) -> Tuple[int, int, int, int]:
        self.update(history_path)
        entry = self.data.get("files", {}).get(str(history_path.resolve()), {})
        rounds = entry.get("rounds") if isinstance(entry, Mapping) else {}
        rows = rounds.get(str(round_index), {}) if isinstance(rounds, Mapping) else {}
        rows = rows if isinstance(rows, Mapping) else {}
        valid = sum(
            1
            for value in rows.values()
            if isinstance(value, Mapping)
            and value.get("status_ok") is True
            and value.get("monitor_ok") is True
            and value.get("excluded_from_valid") is not True
        )
        technical = sum(
            1
            for value in rows.values()
            if isinstance(value, Mapping) and value.get("technical") is True
        )
        budget_exceeded = sum(
            1
            for value in rows.values()
            if isinstance(value, Mapping)
            and value.get("budget_exceeded") is True
        )
        terminal_attempted = sum(
            1
            for value in rows.values()
            if isinstance(value, Mapping)
            and value.get("terminal_attempt") is True
        )
        return terminal_attempted, valid, technical, budget_exceeded

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def scene_status(
    root: Path,
    scene: str,
    incremental_cache: Optional[IncrementalHistoryCache] = None,
) -> Dict[str, Any]:
    scene_root = root / scene
    state_path = scene_root / "algorithm_a_state.json"
    history_path = scene_root / "algorithm_a.jsonl"
    if not state_path.is_file():
        return {
            "scene": scene,
            "phase": "state_pending",
            "accepted": 0,
            "accepted_target": 7,
            "round_index": 0,
            "round_attempted": 0,
            "round_valid": 0,
            "round_technical": 0,
            "round_budget_exceeded": 0,
            "round_expected": 128,
            "round_missing": 128,
            "round_checkpoint_status": None,
            "normalized_width": None,
            "physical": 0,
            "valid": 0,
            "technical": 0,
            "out_of_band": 0,
            "stable": 0,
            "last_event": None,
            "last_event_time": None,
            "terminal_failure": False,
            "complete": False,
        }
    state = read_json(state_path)
    checkpoint = state.get("algorithm_a_round_checkpoint")
    checkpoint = checkpoint if isinstance(checkpoint, Mapping) else {}
    identity_memory = state.get("valid_replay_identity_memory")
    identity_memory = identity_memory if isinstance(identity_memory, Mapping) else {}
    identities = identity_memory.get("identities")
    identities = identities if isinstance(identities, Mapping) else {}
    if incremental_cache is None:
        last = last_jsonl_event(history_path) or {}
    else:
        last = incremental_cache.last_event(history_path) or {}
    phase = str(state.get("phase") or "unknown")
    post_convergence_stability_required = bool(
        state.get("post_convergence_stability_required", True)
    )
    round_index = int(state.get("round_index") or 0)
    if incremental_cache is None:
        (
            round_attempted,
            logged_valid,
            logged_technical,
            logged_budget_exceeded,
        ) = round_replay_counts(history_path, round_index)
    else:
        (
            round_attempted,
            logged_valid,
            logged_technical,
            logged_budget_exceeded,
        ) = incremental_cache.round_counts(history_path, round_index)
    checkpoint_valid = checkpoint.get("valid_candidate_count")
    round_expected = int(checkpoint.get("candidate_count") or 128)
    # ``missing_candidate_count`` is written when a replay batch is admitted.
    # After a clean stop/resume it may therefore describe the pre-resume batch
    # rather than the terminal attempts already present in the append-only
    # history.  The history counter is keyed by the exact parameter vector, so
    # it is the authoritative live progress denominator for Algorithm A.
    round_missing = max(0, round_expected - round_attempted)
    return {
        "scene": scene,
        "phase": phase,
        "accepted": int(state.get("accepted") or 0),
        "accepted_target": int(state.get("accepted_target") or 7),
        "round_index": round_index,
        "normalized_width": state.get("normalized_width"),
        "round_attempted": round_attempted,
        "round_valid": int(
            max(logged_valid, int(checkpoint_valid or 0))
        ),
        # Structured candidate history is authoritative here. Older live
        # controllers counted wall-budget rejections under the broad
        # ``technical_failure`` state counter; do not repeat that ambiguity in
        # monitoring or convergence reports.
        "round_technical": int(logged_technical),
        "round_budget_exceeded": int(logged_budget_exceeded),
        "round_expected": round_expected,
        "round_missing": round_missing,
        "round_checkpoint_status": checkpoint.get("status"),
        "physical": int(state.get("physical_workload_invocations") or 0),
        "valid": int(state.get("valid_replays") or 0),
        "technical": int(state.get("technical_failures") or 0),
        "out_of_band": int(state.get("out_of_band_invalid_replays") or 0),
        "stable": int(state.get("consecutive_stable_rounds") or 0),
        "post_convergence_stability_required": post_convergence_stability_required,
        "last_event": last.get("event"),
        "last_event_time": last.get("time"),
        "terminal_failure": any(marker in phase for marker in TERMINAL_FAILURE_MARKERS),
        "complete": bool(
            phase == "complete"
            and int(state.get("accepted") or 0)
            == int(state.get("accepted_target") or 7)
            and float(state.get("normalized_width") or 1.0) <= 0.01
            and (
                not post_convergence_stability_required
                or (state.get("repeatability") or {}).get("passed") is True
            )
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--incremental-cache",
        type=Path,
        help="Persist an append-only JSONL cursor and replay counts at this path",
    )
    parser.add_argument(
        "--scene-root",
        action="append",
        default=[],
        metavar="SCENE=CAMPAIGN_ROOT",
        help=(
            "Override one scene's campaign root. Repeat for repaired lanes that "
            "continue in a separate fresh campaign."
        ),
    )
    args = parser.parse_args()
    scene_roots: Dict[str, Path] = {}
    for item in args.scene_root:
        scene_name, separator, raw_root = str(item).partition("=")
        if not separator or scene_name not in SCENES or not raw_root:
            parser.error(
                "--scene-root must be SCENE=CAMPAIGN_ROOT for one of: "
                + ", ".join(SCENES)
            )
        scene_roots[scene_name] = Path(raw_root)
    incremental_cache = (
        IncrementalHistoryCache(args.incremental_cache)
        if args.incremental_cache is not None
        else None
    )
    rows: List[Dict[str, Any]] = [
        scene_status(
            scene_roots.get(scene, args.campaign_root),
            scene,
            incremental_cache,
        )
        for scene in SCENES
    ]
    if incremental_cache is not None:
        incremental_cache.save()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2), flush=True)
    else:
        print(
            "scene        accepted  round  progress     valid  infra  budget  remaining  postcheck  phase",
            flush=True,
        )
        for row in rows:
            postcheck = (
                str(row["stable"])
                if row["post_convergence_stability_required"]
                else "n/a"
            )
            print(
                f"{row['scene']:<12} "
                f"{row['accepted']}/{row['accepted_target']:<5} "
                f"{row['round_index']:<6} "
                f"{row['round_attempted']}/{row['round_expected']:<8} "
                f"{row['round_valid']:<6} "
                f"{row['round_technical']:<6} "
                f"{row['round_budget_exceeded']:<7} "
                f"{row['round_missing']:<10} "
                f"{postcheck:<9} "
                f"{row['phase']}",
                flush=True,
            )
            print(
                f"  campaign physical/valid/raw_invalid="
                f"{row['physical']}/{row['valid']}/{row['technical']} "
                f"last={row['last_event']} checkpoint={row['round_checkpoint_status']} "
                f"out_of_band={row['out_of_band']}",
                flush=True,
            )
    return 2 if any(row["terminal_failure"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
