#!/usr/bin/env python3
"""Exact-client successor for the adopted JOB planner/replay services.

The sealed v13 service accidentally defines ``AdoptedPlannerCampaign``'s
``apply_candidate_costs`` twice.  The second definition shadows the exact
client implementation, while ``V2ReplayCampaign`` inherits the legacy local
transport entirely.  This module fixes both paths without changing the sealed
predecessor.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping

import mysql_job_distributed_v2_adopted_services as base


def _apply_exact(campaign: Any, *, require_null: bool) -> None:
    adapter = base.MySQLCostAdapter(campaign.owner.client_for(campaign))
    before = adapter.snapshot()
    base.MySQLCostAdapter.save_snapshot(
        campaign.output / "cost_snapshot_before.json", before
    )
    if not campaign.candidate_costs:
        unexpected = sorted(
            item.name
            for item in before
            if item.name in base.MYSQL_COST_KEYS and item.raw_value is not None
        )
        if require_null and unexpected:
            raise base.AdoptionError(
                f"adopted planner cost overrides are not NULL: {unexpected}"
            )
        return
    adapter.apply(campaign.candidate_costs)
    effective = adapter.effective_values()
    mismatches = {
        name: {"requested": requested, "effective": effective.get(name)}
        for name, requested in campaign.candidate_costs.items()
        if name not in effective
        or not math.isclose(
            float(effective[name]), float(requested), rel_tol=5e-6, abs_tol=1e-8
        )
    }
    if mismatches:
        raise base.AdoptionError(
            "candidate MySQL cost readback differs: "
            + json.dumps(mismatches, sort_keys=True)
        )
    (campaign.output / "candidate_cost_readback.json").write_text(
        json.dumps(
            {
                "schema_version": "mysql_candidate_cost_readback_v1",
                "requested_active_costs": campaign.candidate_costs,
                "effective_all_costs": {
                    name: effective[name] for name in base.MYSQL_COST_KEYS
                },
                "flush_optimizer_costs": True,
                "fresh_connection_readback": True,
                "transport": "exact_client_docker_exec_stdout_v1",
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class ExactAdoptedPlannerCampaign(base.AdoptedPlannerCampaign):
    def apply_candidate_costs(self) -> None:
        _apply_exact(self, require_null=True)


class ExactV2ReplayCampaign(base.V2ReplayCampaign):
    def apply_candidate_costs(self) -> None:
        _apply_exact(self, require_null=False)


class ExactAdoptedProductionServices(base.AdoptedProductionServices):
    def _new_campaign(
        self, lane: Any, role: str, costs: Mapping[str, float] | None = None
    ) -> Any:
        run_id = self._run_id(role)
        common = {
            "candidate_costs": costs,
            "measurement_seconds": int(self.config.get("measurement_seconds", 600)),
            "minimum_measurement_seconds": (
                int(self.config.get("measurement_seconds", 600))
                if role == "r0"
                else int(self.config.get("minimum_measurement_seconds", 180))
            ),
            "minimum_client_completions": int(
                self.config.get("minimum_client_completions", 0)
            ),
            "require_r0_profile_gate": role == "r0",
            "authority": self.authority,
            "config": self.config,
            "owner": self,
        }
        campaign_type = (
            ExactAdoptedPlannerCampaign
            if role in {"r0", "resume_planner"}
            else ExactV2ReplayCampaign
        )
        return campaign_type(lane, self.points / run_id, run_id, **common)


def build(config: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        return ExactAdoptedProductionServices(config).bundle()
    except base.AdoptionError as exc:
        raise base.FormalSGDNotReady(str(exc)) from exc
