"""Physical-wave isolation for the explicitly scoped JOB/XP-I/O C revision."""

from __future__ import annotations

import copy
import subprocess
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict

from shell import run_pg_common_domain_frozen_champions as replay
from shell.pg_fixed_guc_guard import audit_fixed_guc_domain

CONTRACT = "algorithm_c_guarded_isolated_search_v2"
UNCONTROLLED_EXTERNAL_POLICY = "user_authorized_uncontrolled_external_workloads_v1"


class IsolatedWaveGuard:
    def __init__(self, controller: Any) -> None:
        self.controller = controller
        self.config = controller.algorithm_cfg["isolated_search"]
        if self.config.get("contract") != CONTRACT or self.config.get("enabled") is not True:
            raise ValueError("explicit isolated-search contract required")
        self.scene = controller.algorithm_cfg["scene"]
        if self.scene not in {"job_memory", "xp_io"}:
            raise ValueError("isolated revision is limited to JOB-Memory and XP-I/O")
        self.contracts = {self.scene: {
            "cfg": controller.cfg, "workers": controller.workers, "target": controller.target,
        }}
        self.root = controller.output_dir / "isolation"
        self.root.mkdir(parents=True, exist_ok=True)
        self.thread = None
        self.stop_event = threading.Event()
        self.receipt: Dict[str, Any] = {}
        self.label = "initial"
        self.owned_backend_roots: set[int] = set()
        self.external_guard_required = self.config.get("external_workload_guard_required") is True
        if not self.external_guard_required and (
            self.config.get("external_workload_interference_policy") != UNCONTROLLED_EXTERNAL_POLICY
            or self.config.get("external_workload_interference_user_accepted") is not True
        ):
            raise ValueError("uncontrolled external workloads require explicit user acceptance")

    def resolve_owned_backend_roots(self) -> set[int]:
        containers = [str(worker.get("container") or "") for worker in self.controller.workers]
        if not containers or any(not value for value in containers):
            raise RuntimeError("declared worker containers are required for backend ownership")
        completed = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Pid}}", *containers],
            check=False, capture_output=True, text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"cannot resolve declared worker container PIDs: {completed.stderr.strip()}")
        try:
            roots = {int(value) for value in completed.stdout.splitlines() if value.strip()}
        except ValueError as exc:
            raise RuntimeError("invalid declared worker container PID") from exc
        if len(roots) != len(containers) or any(value <= 1 for value in roots):
            raise RuntimeError("declared worker container PID ownership is incomplete")
        self.owned_backend_roots = roots
        return roots

    def measure(self, category: str, label: str):
        recorder = getattr(self.controller, "efficiency_recorder", None)
        return recorder.measure(category, label) if recorder is not None else nullcontext()

    def audit(self, label: str) -> Dict[str, Any]:
        with self.measure("domain_audit", label):
            return self._audit(label)

    def _audit(self, label: str) -> Dict[str, Any]:
        controller = self.controller
        gucs = audit_fixed_guc_domain(
            controller.cfg, controller.workers,
            expected_semantic_sha256=self.config["expected_non_search_guc_sha256"],
        )
        resources = replay.audit_container_resource_domain(self.contracts)
        result = {"non_search_gucs": gucs, "container_resources": resources}
        replay.write_json(self.root / f"{label}_domain.json", result)
        return result

    def before_restore(self, label: str) -> None:
        if self.thread is not None:
            raise RuntimeError("previous physical wave guard was not closed")
        self.label = label
        roots = self.resolve_owned_backend_roots()
        if self.external_guard_required:
            with self.measure("external_quiet_wait", label):
                ready = replay.wait_for_external_quiet(
                    self.root, label, quiet_s=120.0,
                    should_stop=lambda: self.controller.stop_requested,
                    ignored_backend_ancestor_pids=roots,
                )
            if not ready:
                raise RuntimeError("isolated C stopped while waiting for external workloads")
        self.audit(f"{label}_before")
        self.receipt = {"contract": replay.EXTERNAL_WORKLOAD_GUARD_CONTRACT,
                        "label": label, "started_at_epoch_s": time.time(),
                        "owned_backend_root_pids": sorted(roots),
                        "owned_backend_exclusion_scope": "declared_container_postmaster_descendants_only",
                        "external_workload_guard_required": self.external_guard_required}
        if not self.external_guard_required:
            self.receipt.update({
                "passed": True, "contaminated": False,
                "external_workload_interference_policy": UNCONTROLLED_EXTERNAL_POLICY,
                "external_workloads_observed": "not_monitored_by_user_instruction",
            })
            return
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=replay.monitor_external_workloads,
            args=(self.stop_event, self.receipt, 1.0, roots), daemon=True,
        )
        self.thread.start()

    def after_restore(self, _unused: Any) -> None:
        with self.measure("post_restore_quiet_wait", self.label):
            quiet = replay.wait_for_post_restore_ssd_quiet(
                device="nvme1n1", sample_s=15.0, maximum_util_pct=5.0, maximum_wait_s=600.0,
            )
        replay.write_json(self.root / f"{self.label}_post_restore_quiet.json", quiet)
        matches, errors = (replay.scan_external_workload_processes(
            ignored_backend_ancestor_pids=self.owned_backend_roots,
        ) if self.external_guard_required else ([], []))
        if quiet.get("passed") is not True or matches or errors or self.receipt.get("contaminated"):
            self.close()
            raise RuntimeError("isolated C restore/quiet interval contaminated; no workload admitted")
        self.audit(f"{self.label}_post_restore")

    def close(self) -> None:
        if self.thread is None:
            if self.receipt and not self.external_guard_required:
                self.receipt.setdefault("finished_at_epoch_s", time.time())
                replay.write_json(self.root / f"{self.label}_external_guard.json", self.receipt)
            return
        self.stop_event.set()
        self.thread.join(timeout=5.0)
        if self.thread.is_alive():
            raise RuntimeError("isolated C external-workload monitor failed to stop")
        self.thread = None
        replay.write_json(self.root / f"{self.label}_external_guard.json", self.receipt)

    def after_replay(self, rows: list[Dict[str, Any]]) -> None:
        self.close()
        domain_error = None
        try:
            self.audit(f"{self.label}_after")
        except (RuntimeError, ValueError) as exc:
            domain_error = str(exc)
        for row in rows:
            row["isolated_search_guard"] = copy.deepcopy(self.receipt)
            if self.receipt.get("passed") is not True or domain_error:
                row["technical_failure"] = True
                row["status"] = "isolated_search_domain_failure"
                row.setdefault("errors", []).append(domain_error or "external workload contamination")


def isolated_environment(scene: str, root: Path) -> Dict[str, str]:
    """One active scene at a time, with its unchanged full paper workload."""
    if scene not in {"job_memory", "xp_io"}:
        raise ValueError("unsupported isolated scene")
    return {
        **replay.workload_environment(scene, root),
        "BONECT_SHARED_SSD_COORDINATION_ENABLED": "0",
        "BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE": "none",
        "BONECT_ALGORITHM_C_BASELINE_COHORT_ID": "",
        "BONECT_ALGORITHM_C_BASELINE_COHORT_SIZE": "1",
        "BONECT_ALGORITHM_C_CANDIDATE_COHORT_ID": "",
        "BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE": "1",
        "PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM": "2",
    }
