"""Checksummed outer-level resume ledger for formal MySQL SGD.

This module is filesystem-only.  It seals candidate/representative order and
records every pruning, clustering, suppression and 5->10->K replay decision so
a resume cannot rerun R0, a completed subset, or any already replayed rank.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import uuid
from typing import Any, Mapping, Sequence


class FormalReplayCheckpointError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _candidate_signature(candidate: Mapping[str, Any]) -> dict[str, Any]:
    rank = candidate.get("rank")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
        raise FormalReplayCheckpointError("candidate rank must be a positive integer")
    params = candidate.get("optimized_params")
    if not isinstance(params, Mapping) or not params:
        raise FormalReplayCheckpointError(f"candidate rank {rank} lacks optimized_params")
    encoded: dict[str, str] = {}
    for name, value in sorted(params.items()):
        if not isinstance(name, str) or not name:
            raise FormalReplayCheckpointError("candidate parameter name is invalid")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise FormalReplayCheckpointError(f"candidate rank {rank} parameter {name} is not finite")
        encoded[name] = float(value).hex()
    canonical_plan_bundle_sha256 = (
        (candidate.get("canonical_plan_bundle") or {}).get(
            "canonical_plan_bundle_sha256"
        )
        if isinstance(candidate.get("canonical_plan_bundle"), Mapping)
        else (
            ((candidate.get("final_estimation_metadata") or {}).get(
                "canonical_plan_bundle"
            ) or {}).get("canonical_plan_bundle_sha256")
            if isinstance(candidate.get("final_estimation_metadata"), Mapping)
            else None
        )
    )
    if (
        not isinstance(canonical_plan_bundle_sha256, str)
        or len(canonical_plan_bundle_sha256) != 64
        or any(character not in "0123456789abcdef" for character in canonical_plan_bundle_sha256)
    ):
        raise FormalReplayCheckpointError(
            f"candidate rank {rank} lacks a canonical plan bundle SHA-256"
        )
    return {
        "rank": rank,
        "subset_id": candidate.get("subset_id"),
        "strict_plan_cluster_key": candidate.get("strict_plan_cluster_key"),
        "canonical_plan_bundle_sha256": canonical_plan_bundle_sha256,
        "optimized_params_float_hex": encoded,
    }


class FormalProductionR0Ledger:
    """Checksummed, identity-bound evidence for the real production R0.

    This is deliberately separate from ``FormalOuterReplayLedger.r0_result``:
    that value caches an estimator evaluation inside one outer round, whereas
    this ledger protects the fresh database measurement made by the production
    campaign entry point.
    """

    SCHEMA_VERSION = "mysql_sgd_production_real_r0_checkpoint_v1"

    @classmethod
    def create(
        cls, path: Path, *, campaign_identity: Mapping[str, Any],
        measurement: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = Path(path)
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise FormalReplayCheckpointError(
                "production real R0 checkpoint already exists; load and verify it"
            )
        if not isinstance(measurement, Mapping):
            raise FormalReplayCheckpointError("production real R0 must be a mapping")
        frozen_measurement = copy.deepcopy(dict(measurement))
        document = {
            "schema_version": cls.SCHEMA_VERSION,
            "measurement_kind": "production_real_database_r0",
            "campaign_identity": copy.deepcopy(dict(campaign_identity)),
            "measurement": frozen_measurement,
            "measurement_sha256": hashlib.sha256(_canonical(frozen_measurement)).hexdigest(),
        }
        payload = _canonical(document)
        digest = hashlib.sha256(payload).hexdigest()
        _atomic(path, payload)
        _atomic(
            path.with_name(path.name + ".sha256"),
            f"{digest}  {path.name}\n".encode("ascii"),
        )
        return copy.deepcopy(frozen_measurement)

    @classmethod
    def load_verified(
        cls, path: Path, *, campaign_identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = Path(path)
        sidecar = path.with_name(path.name + ".sha256")
        if not path.is_file() or path.is_symlink() or not sidecar.is_file() or sidecar.is_symlink():
            raise FormalReplayCheckpointError(
                "production real R0 checkpoint or checksum sidecar is absent/symlink"
            )
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if sidecar.read_text(encoding="ascii") != f"{digest}  {path.name}\n":
            raise FormalReplayCheckpointError("production real R0 checkpoint checksum mismatch")
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise FormalReplayCheckpointError("production real R0 checkpoint JSON is invalid") from exc
        if document.get("schema_version") != cls.SCHEMA_VERSION:
            raise FormalReplayCheckpointError("production real R0 checkpoint schema mismatch")
        if document.get("measurement_kind") != "production_real_database_r0":
            raise FormalReplayCheckpointError("production real R0 measurement kind mismatch")
        if document.get("campaign_identity") != copy.deepcopy(dict(campaign_identity)):
            raise FormalReplayCheckpointError("production real R0 campaign identity mismatch")
        measurement = document.get("measurement")
        if not isinstance(measurement, Mapping):
            raise FormalReplayCheckpointError("production real R0 measurement is absent")
        if document.get("measurement_sha256") != hashlib.sha256(_canonical(measurement)).hexdigest():
            raise FormalReplayCheckpointError("production real R0 measurement checksum mismatch")
        return copy.deepcopy(dict(measurement))


class FormalOuterReplayLedger:
    SCHEMA_VERSION = "mysql_sgd_outer_replay_checkpoint_v1"

    def __init__(self, path: Path, document: Mapping[str, Any]) -> None:
        self.path = Path(path)
        self.document = copy.deepcopy(dict(document))

    @classmethod
    def create_campaign(
        cls, path: Path, *, outer_iter: int, campaign_identity: Mapping[str, Any],
        ordered_subset_ids: Sequence[str],
    ) -> "FormalOuterReplayLedger":
        subsets = list(ordered_subset_ids)
        if len(subsets) != len(set(subsets)) or any(not isinstance(item, str) or not item for item in subsets):
            raise FormalReplayCheckpointError("subset schedule must contain unique non-empty IDs")
        document = {
            "schema_version": cls.SCHEMA_VERSION,
            "outer_iter": outer_iter,
            "campaign_identity": copy.deepcopy(dict(campaign_identity)),
            "ordered_subset_ids": subsets,
            "r0_completed": False,
            "r0_result": None,
            "completed_subset_ids": [],
            "completed_subset_results": {},
            "sealed_candidate_order": None,
            "sealed_representative_ranks": None,
            "sealed_representative_order_sha256": None,
            "replayed_candidate_ranks": [],
            "inflight_replays": {},
            "audit_log": [{"event": "campaign_and_subset_schedule_sealed"}],
        }
        ledger = cls(path, document)
        ledger._validate_campaign(campaign_identity, subsets)
        ledger._write()
        return ledger

    @classmethod
    def load_campaign_verified(
        cls, path: Path, *, campaign_identity: Mapping[str, Any],
        ordered_subset_ids: Sequence[str],
    ) -> "FormalOuterReplayLedger":
        ledger = cls._load_checksum_verified(path)
        ledger._validate_campaign(campaign_identity, list(ordered_subset_ids))
        return ledger

    @classmethod
    def _load_checksum_verified(cls, path: Path) -> "FormalOuterReplayLedger":
        path = Path(path)
        sidecar = path.with_name(path.name + ".sha256")
        if not path.is_file() or path.is_symlink() or not sidecar.is_file() or sidecar.is_symlink():
            raise FormalReplayCheckpointError("checkpoint or checksum sidecar is absent/symlink")
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if sidecar.read_text(encoding="ascii") != f"{digest}  {path.name}\n":
            raise FormalReplayCheckpointError("checkpoint checksum mismatch")
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise FormalReplayCheckpointError("checkpoint JSON is invalid") from exc
        return cls(path, document)

    @classmethod
    def create(
        cls, path: Path, *, outer_iter: int, ranked_candidates: Sequence[Mapping[str, Any]],
        representative_ranks: Sequence[int], completed_subset_ids: Sequence[str] = (),
        completed_subset_results: Mapping[str, Mapping[str, Any]] | None = None,
        r0_completed: bool = False, r0_result: Mapping[str, Any] | None = None,
        pruning_log: Sequence[Mapping[str, Any]] = (),
        plan_cluster_log: Sequence[Mapping[str, Any]] = (),
        representative_log: Sequence[Mapping[str, Any]] = (),
        suppression_log: Sequence[Mapping[str, Any]] = (),
    ) -> "FormalOuterReplayLedger":
        if isinstance(outer_iter, bool) or not isinstance(outer_iter, int) or outer_iter < 1:
            raise FormalReplayCheckpointError("outer_iter must be a positive integer")
        signatures = [_candidate_signature(candidate) for candidate in ranked_candidates]
        ranks = [row["rank"] for row in signatures]
        if len(ranks) != len(set(ranks)) or ranks != sorted(ranks):
            raise FormalReplayCheckpointError("ranked candidates require unique ascending ranks")
        representatives = list(representative_ranks)
        if (
            not representatives or len(representatives) != len(set(representatives))
            or any(rank not in set(ranks) for rank in representatives)
        ):
            raise FormalReplayCheckpointError("representative ranks must be unique sealed candidate ranks")
        subsets = list(completed_subset_ids)
        if len(subsets) != len(set(subsets)) or any(not isinstance(item, str) or not item for item in subsets):
            raise FormalReplayCheckpointError("completed subset IDs must be unique non-empty strings")
        subset_results = copy.deepcopy(dict(completed_subset_results or {}))
        if set(subset_results) != set(subsets):
            raise FormalReplayCheckpointError("completed subset results must match completed IDs")
        if bool(r0_completed) != isinstance(r0_result, Mapping):
            raise FormalReplayCheckpointError("R0 completion and restorable result must agree")
        order_sha = hashlib.sha256(_canonical(representatives)).hexdigest()
        audit_log = [
            {"event": "pruning_decisions_sealed", "records": copy.deepcopy(list(pruning_log))},
            {"event": "plan_clusters_sealed", "records": copy.deepcopy(list(plan_cluster_log))},
            {"event": "representatives_sealed", "ordered_ranks": representatives, "records": copy.deepcopy(list(representative_log)), "order_sha256": order_sha},
            {"event": "suppressed_candidates_sealed", "records": copy.deepcopy(list(suppression_log))},
            {"event": "replay_stage_schedule_sealed", "stages": [
                {"stage": "top_5", "limit": min(5, len(representatives))},
                {"stage": "top_10", "limit": min(10, len(representatives))},
                {"stage": "top_k", "limit": len(representatives)},
            ]},
        ]
        document = {
            "schema_version": cls.SCHEMA_VERSION,
            "outer_iter": outer_iter,
            "r0_completed": bool(r0_completed),
            "r0_result": copy.deepcopy(dict(r0_result)) if isinstance(r0_result, Mapping) else None,
            "ordered_subset_ids": subsets,
            "completed_subset_ids": subsets,
            "completed_subset_results": subset_results,
            "sealed_candidate_order": signatures,
            "sealed_representative_ranks": representatives,
            "sealed_representative_order_sha256": order_sha,
            "replayed_candidate_ranks": [],
            "inflight_replays": {},
            "audit_log": audit_log,
        }
        ledger = cls(path, document)
        ledger._write()
        return ledger

    @classmethod
    def load_verified(
        cls, path: Path, ranked_candidates: Sequence[Mapping[str, Any]],
    ) -> "FormalOuterReplayLedger":
        ledger = cls._load_checksum_verified(path)
        ledger._validate(ranked_candidates)
        return ledger

    def _validate_campaign(
        self, campaign_identity: Mapping[str, Any], ordered_subset_ids: Sequence[str],
    ) -> None:
        if self.document.get("schema_version") != self.SCHEMA_VERSION:
            raise FormalReplayCheckpointError("checkpoint schema mismatch")
        if self.document.get("campaign_identity") != copy.deepcopy(dict(campaign_identity)):
            raise FormalReplayCheckpointError("campaign identity differs from sealed checkpoint")
        if self.document.get("ordered_subset_ids") != list(ordered_subset_ids):
            raise FormalReplayCheckpointError("subset schedule differs from sealed checkpoint")
        completed = self.document.get("completed_subset_ids")
        results = self.document.get("completed_subset_results")
        if not isinstance(completed, list) or len(completed) != len(set(completed)):
            raise FormalReplayCheckpointError("completed subset IDs are malformed")
        if not isinstance(results, Mapping) or set(results) != set(completed):
            raise FormalReplayCheckpointError("completed subset results are incomplete")
        if any(item not in set(ordered_subset_ids) for item in completed):
            raise FormalReplayCheckpointError("completed subset is outside sealed schedule")
        if self.document.get("r0_completed") is True and not isinstance(self.document.get("r0_result"), Mapping):
            raise FormalReplayCheckpointError("completed R0 lacks restorable result")

    def _validate(self, ranked_candidates: Sequence[Mapping[str, Any]]) -> None:
        document = self.document
        if document.get("schema_version") != self.SCHEMA_VERSION:
            raise FormalReplayCheckpointError("checkpoint schema mismatch")
        current = [_candidate_signature(candidate) for candidate in ranked_candidates]
        if document.get("sealed_candidate_order") != current:
            raise FormalReplayCheckpointError("current candidate order differs from sealed order")
        representatives = document.get("sealed_representative_ranks")
        if not isinstance(representatives, list) or (not representatives and current):
            raise FormalReplayCheckpointError("sealed representative order is absent")
        digest = hashlib.sha256(_canonical(representatives)).hexdigest()
        if document.get("sealed_representative_order_sha256") != digest:
            raise FormalReplayCheckpointError("sealed representative order checksum mismatch")
        candidate_ranks = {row["rank"] for row in current}
        if len(representatives) != len(set(representatives)) or any(rank not in candidate_ranks for rank in representatives):
            raise FormalReplayCheckpointError("sealed representative ranks are invalid")
        replayed = document.get("replayed_candidate_ranks")
        if not isinstance(replayed, list) or len(replayed) != len(set(replayed)):
            raise FormalReplayCheckpointError("replayed_candidate_ranks are malformed")
        if any(rank not in set(representatives) for rank in replayed):
            raise FormalReplayCheckpointError("replayed rank is outside sealed representatives")
        inflight = document.get("inflight_replays")
        if not isinstance(inflight, Mapping):
            raise FormalReplayCheckpointError("inflight replay map is malformed")
        inflight_ranks = []
        for token, record in inflight.items():
            if not isinstance(token, str) or not token or not isinstance(record, Mapping):
                raise FormalReplayCheckpointError("inflight replay record is malformed")
            rank = record.get("rank")
            if rank not in set(representatives) or rank in set(replayed):
                raise FormalReplayCheckpointError("inflight replay rank is invalid")
            inflight_ranks.append(rank)
        if len(inflight_ranks) != len(set(inflight_ranks)):
            raise FormalReplayCheckpointError("duplicate inflight replay rank")
        subsets = document.get("completed_subset_ids")
        if not isinstance(subsets, list) or len(subsets) != len(set(subsets)):
            raise FormalReplayCheckpointError("completed subset IDs are malformed")
        events = [row.get("event") for row in document.get("audit_log", []) if isinstance(row, Mapping)]
        required = {
            "pruning_decisions_sealed", "plan_clusters_sealed", "representatives_sealed",
            "suppressed_candidates_sealed", "replay_stage_schedule_sealed",
        }
        if not required <= set(events):
            raise FormalReplayCheckpointError("checkpoint lacks full pruning/cluster/replay audit")

    def seal_replay(
        self, *, ranked_candidates: Sequence[Mapping[str, Any]],
        representative_ranks: Sequence[int], pruning_log: Sequence[Mapping[str, Any]] = (),
        plan_cluster_log: Sequence[Mapping[str, Any]] = (),
        representative_log: Sequence[Mapping[str, Any]] = (),
        suppression_log: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        signatures = [_candidate_signature(candidate) for candidate in ranked_candidates]
        ranks = [row["rank"] for row in signatures]
        representatives = list(representative_ranks)
        if len(ranks) != len(set(ranks)) or ranks != sorted(ranks):
            raise FormalReplayCheckpointError("ranked candidates require unique ascending ranks")
        if ((not representatives and ranks) or len(representatives) != len(set(representatives)) or any(rank not in set(ranks) for rank in representatives)):
            raise FormalReplayCheckpointError("representative ranks must be unique sealed candidate ranks")
        if self.document.get("sealed_candidate_order") is not None:
            self._validate(ranked_candidates)
            if self.document.get("sealed_representative_ranks") != representatives:
                raise FormalReplayCheckpointError("representative order differs from sealed checkpoint")
            return
        order_sha = hashlib.sha256(_canonical(representatives)).hexdigest()
        self.document.update({
            "sealed_candidate_order": signatures,
            "sealed_representative_ranks": representatives,
            "sealed_representative_order_sha256": order_sha,
        })
        self.document["audit_log"].extend([
            {"event": "pruning_decisions_sealed", "records": copy.deepcopy(list(pruning_log))},
            {"event": "plan_clusters_sealed", "records": copy.deepcopy(list(plan_cluster_log))},
            {"event": "representatives_sealed", "ordered_ranks": representatives, "records": copy.deepcopy(list(representative_log)), "order_sha256": order_sha},
            {"event": "suppressed_candidates_sealed", "records": copy.deepcopy(list(suppression_log))},
            {"event": "replay_stage_schedule_sealed", "stages": [
                {"stage": "top_5", "limit": min(5, len(representatives))},
                {"stage": "top_10", "limit": min(10, len(representatives))},
                {"stage": "top_k", "limit": len(representatives)},
            ]},
        ])
        self._write()
        self._validate(ranked_candidates)

    def _write(self) -> None:
        payload = _canonical(self.document)
        digest = hashlib.sha256(payload).hexdigest()
        _atomic(self.path, payload)
        _atomic(
            self.path.with_name(self.path.name + ".sha256"),
            f"{digest}  {self.path.name}\n".encode("ascii"),
        )

    def assert_r0_may_run(self) -> None:
        if self.document.get("r0_completed") is True:
            raise FormalReplayCheckpointError("R0 is already complete and must not rerun")

    def mark_r0_completed(self, result: Mapping[str, Any] | None = None) -> None:
        self.assert_r0_may_run()
        if not isinstance(result, Mapping):
            raise FormalReplayCheckpointError("R0 completion requires a restorable result")
        self.document["r0_completed"] = True
        self.document["r0_result"] = copy.deepcopy(dict(result))
        self.document["audit_log"].append({"event": "r0_completed"})
        self._write()

    def restored_r0_result(self) -> dict[str, Any] | None:
        result = self.document.get("r0_result")
        return copy.deepcopy(dict(result)) if isinstance(result, Mapping) else None

    def pending_subsets(self, ordered_subset_ids: Sequence[str]) -> list[str]:
        if len(ordered_subset_ids) != len(set(ordered_subset_ids)):
            raise FormalReplayCheckpointError("subset schedule contains duplicates")
        completed = set(self.document["completed_subset_ids"])
        return [subset for subset in ordered_subset_ids if subset not in completed]

    def mark_subset_completed(self, subset_id: str, result: Mapping[str, Any] | None = None) -> None:
        if subset_id in set(self.document["completed_subset_ids"]):
            raise FormalReplayCheckpointError(f"subset {subset_id} is already complete and must not rerun")
        if subset_id not in set(self.document.get("ordered_subset_ids") or [subset_id]):
            raise FormalReplayCheckpointError("subset is outside sealed schedule")
        if not isinstance(result, Mapping):
            raise FormalReplayCheckpointError("subset completion requires a restorable result")
        self.document["completed_subset_ids"].append(subset_id)
        self.document["completed_subset_results"][subset_id] = copy.deepcopy(dict(result))
        self.document["audit_log"].append({"event": "subset_completed", "subset_id": subset_id})
        self._write()

    def restored_subset_results(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.document.get("completed_subset_results") or {}))

    def pending_replay_ranks(self, limit: int) -> list[int]:
        representatives = self.document["sealed_representative_ranks"]
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise FormalReplayCheckpointError("replay limit must be positive")
        replayed = set(self.document["replayed_candidate_ranks"])
        inflight = {
            record.get("rank")
            for record in self.document.get("inflight_replays", {}).values()
            if isinstance(record, Mapping)
        }
        if any(rank in set(representatives[:limit]) for rank in inflight):
            raise FormalReplayCheckpointError(
                "unreconciled started replay must not be scheduled again"
            )
        return [rank for rank in representatives[:limit] if rank not in replayed]

    def start_replay(
        self, rank: int, *, stage: str, candidate: Mapping[str, Any],
    ) -> str:
        if rank not in self.document["sealed_representative_ranks"]:
            raise FormalReplayCheckpointError("started rank is not a sealed representative")
        if rank in set(self.document["replayed_candidate_ranks"]):
            raise FormalReplayCheckpointError("completed rank must not start again")
        if stage not in {"top_5", "top_10", "top_k"}:
            raise FormalReplayCheckpointError("unknown replay stage")
        if any(
            record.get("rank") == rank
            for record in self.document.get("inflight_replays", {}).values()
            if isinstance(record, Mapping)
        ):
            raise FormalReplayCheckpointError("rank already has an inflight replay")
        token = uuid.uuid4().hex
        record = {
            "attempt_token": token, "rank": rank, "stage": stage,
            "candidate": copy.deepcopy(dict(candidate)),
        }
        self.document["inflight_replays"][token] = record
        self.document["audit_log"].append({
            "event": "candidate_replay_started", **copy.deepcopy(record),
        })
        self._write()
        return token

    def inflight_replays(self) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(dict(record))
            for record in self.document.get("inflight_replays", {}).values()
            if isinstance(record, Mapping)
        ]

    def mark_replayed(
        self, rank: int, *, stage: str, evidence: Mapping[str, Any],
        attempt_token: str | None = None,
    ) -> None:
        if rank not in self.document["sealed_representative_ranks"]:
            raise FormalReplayCheckpointError("replayed rank is not a sealed representative")
        if rank in set(self.document["replayed_candidate_ranks"]):
            raise FormalReplayCheckpointError(f"rank {rank} was already replayed and must not rerun")
        if stage not in {"top_5", "top_10", "top_k"}:
            raise FormalReplayCheckpointError("unknown replay stage")
        matching_tokens = [
            token for token, record in self.document["inflight_replays"].items()
            if isinstance(record, Mapping) and record.get("rank") == rank
        ]
        if matching_tokens and attempt_token is None:
            raise FormalReplayCheckpointError(
                "inflight replay completion requires its attempt token"
            )
        if attempt_token is not None:
            inflight = self.document["inflight_replays"].get(attempt_token)
            if not isinstance(inflight, Mapping) or inflight.get("rank") != rank or inflight.get("stage") != stage:
                raise FormalReplayCheckpointError("replay completion does not match started token")
            del self.document["inflight_replays"][attempt_token]
        self.document["replayed_candidate_ranks"].append(rank)
        self.document["audit_log"].append({
            "event": "candidate_replayed", "rank": rank, "stage": stage,
            "attempt_token": attempt_token, "evidence": copy.deepcopy(dict(evidence)),
        })
        self._write()

    def replayed_evaluations(self) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(dict(row["evidence"]))
            for row in self.document.get("audit_log", [])
            if isinstance(row, Mapping) and row.get("event") == "candidate_replayed"
            and isinstance(row.get("evidence"), Mapping)
        ]

    def approved_root_cause_review(self) -> dict[str, Any] | None:
        for row in reversed(self.document.get("audit_log", [])):
            if (
                isinstance(row, Mapping)
                and row.get("event") == "root_cause_review_before_top_k"
                and isinstance(row.get("review"), Mapping)
                and row["review"].get("approved") is True
            ):
                return copy.deepcopy(dict(row))
        return None

    def mark_root_cause_review(
        self, *, diagnostic: Mapping[str, Any], review: Mapping[str, Any],
    ) -> None:
        if review.get("approved") is not True:
            raise FormalReplayCheckpointError("root-cause review is not approved")
        if self.approved_root_cause_review() is not None:
            raise FormalReplayCheckpointError("root-cause review was already recorded")
        self.document["audit_log"].append({
            "event": "root_cause_review_before_top_k",
            "diagnostic": copy.deepcopy(dict(diagnostic)),
            "review": copy.deepcopy(dict(review)),
        })
        self._write()

    def mark_replay_technical_failure(
        self, rank: int, *, stage: str, error: str,
        attempt_token: str | None = None,
    ) -> None:
        if rank not in self.document["sealed_representative_ranks"]:
            raise FormalReplayCheckpointError("technical failure rank is not sealed")
        matching_tokens = [
            token for token, record in self.document["inflight_replays"].items()
            if isinstance(record, Mapping) and record.get("rank") == rank
        ]
        if matching_tokens and attempt_token is None:
            raise FormalReplayCheckpointError(
                "inflight technical failure requires its attempt token"
            )
        if attempt_token is not None:
            inflight = self.document["inflight_replays"].get(attempt_token)
            if not isinstance(inflight, Mapping) or inflight.get("rank") != rank:
                raise FormalReplayCheckpointError("technical failure token mismatch")
            del self.document["inflight_replays"][attempt_token]
        self.document["audit_log"].append({
            "event": "candidate_replay_technical_failure", "rank": rank,
            "stage": stage, "attempt_token": attempt_token,
            "error": str(error), "counted_as_replayed": False,
        })
        self._write()


__all__ = ["FormalOuterReplayLedger", "FormalReplayCheckpointError"]
