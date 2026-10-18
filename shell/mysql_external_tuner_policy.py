#!/usr/bin/env python3
"""Generic GPTuner/AQETuner policies for the frozen MySQL cost domain.

The policies only generate candidates.  Physical replay, validity, QPS and
resource accounting remain the responsibility of the MySQL formal harness.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Mapping, MutableSequence, Sequence, Tuple

import numpy as np
from scipy.stats import qmc


GPTUNER_COARSE_TRIALS = 10
AQETUNER_WARM_SAMPLES = 9


def exact_signature(params: Mapping[str, float]) -> str:
    """Return a stable signature without lossy decimal binning."""
    return json.dumps(
        {name: float(params[name]) for name in sorted(params)},
        sort_keys=True,
        separators=(",", ":"),
    )


def git_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


class GPTunerPolicy:
    """Knowledge-shaped coarse-to-fine SMAC policy used by GPTuner."""

    def __init__(
        self,
        *,
        scene: str,
        active_params: Sequence[str],
        ranges: Mapping[str, Tuple[float, float]],
        defaults: Mapping[str, float],
        observations: MutableSequence[Dict[str, Any]],
        candidate_budget: int,
        seed: int,
        official_root: Path,
        publication_mode: bool = False,
        knowledge_snapshot: Path | None = None,
        expected_llm_model: str = "gpt-4",
        expected_llm_provider: str = "openai-api",
        allow_model_substitution: bool = False,
    ) -> None:
        from ConfigSpace import Categorical, Configuration, ConfigurationSpace, Float

        if not hasattr(Configuration, "is_valid_configuration"):
            def is_valid_configuration(configuration: Any) -> bool:
                try:
                    configuration.check_valid_configuration()
                except ValueError:
                    return False
                return True

            setattr(Configuration, "is_valid_configuration", is_valid_configuration)

        from smac import HyperparameterOptimizationFacade, Scenario
        from smac.initial_design import LatinHypercubeInitialDesign
        from smac.runhistory.dataclasses import TrialInfo, TrialValue

        self.scene = scene
        self.active_params = tuple(active_params)
        self.ranges = dict(ranges)
        self.defaults = dict(defaults)
        self.observations = observations
        self.candidate_budget = candidate_budget
        self.seed = seed
        self.official_root = official_root
        self.publication_mode = publication_mode
        self.expected_llm_model = expected_llm_model
        self.expected_llm_provider = expected_llm_provider
        self.allow_model_substitution = allow_model_substitution
        if (
            publication_mode
            and expected_llm_model != "gpt-4"
            and not allow_model_substitution
        ):
            raise RuntimeError("non-paper GPTuner model requires explicit substitution opt-in")
        self.knowledge_snapshot = (
            knowledge_snapshot.resolve() if knowledge_snapshot is not None else None
        )
        self.knowledge_manifest: Dict[str, Any] = {}
        self.TrialInfo = TrialInfo
        self.TrialValue = TrialValue
        self.pending: Dict[str, Tuple[Any, Any]] = {}
        self.incompatible_asks_skipped: List[Dict[str, Any]] = []

        if self.publication_mode and self.knowledge_snapshot is None:
            raise RuntimeError("publication MySQL GPTuner requires online GPT-4 knowledge")
        if self.knowledge_snapshot is not None:
            manifest_path = self.knowledge_snapshot / "manifest.json"
            self.knowledge_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            required = {
                "contract": "gptuner_online_knowledge_snapshot_v1",
                "status": "complete",
                "model": self.expected_llm_model,
                "provider": self.expected_llm_provider,
                "dbms": "mysql",
                "scene": self.scene,
                "active_params": list(self.active_params),
            }
            for key, expected in required.items():
                if self.knowledge_manifest.get(key) != expected:
                    raise RuntimeError(f"MySQL GPTuner knowledge mismatch for {key}")
            if self.expected_llm_model != "gpt-4":
                if not self.allow_model_substitution:
                    raise RuntimeError("MySQL GPTuner model substitution is not authorized")
                if self.knowledge_manifest.get("paper_model_equivalent") is not False:
                    raise RuntimeError("substituted MySQL GPTuner snapshot claims paper equivalence")
            if (self.knowledge_manifest.get("stages") or {}).get(
                "prompt_ensemble_votes"
            ) != 5:
                raise RuntimeError("MySQL GPTuner knowledge lacks five-vote ensemble")
            knowledge_dir = self.knowledge_snapshot / "structured_knowledge/normal"
        else:
            knowledge_dir = (
                official_root
                / "knowledge_collection/mysql/structured_knowledge/normal"
            )
        self.coarse_space = ConfigurationSpace(seed=seed)
        self.knowledge_summary: Dict[str, Any] = {}
        for name in self.active_params:
            low, high = self.ranges[name]
            default = self.defaults[name]
            path = knowledge_dir / f"{name}.json"
            data: Mapping[str, Any] = {}
            if path.is_file():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, Mapping):
                    data = loaded
            suggested: List[float] = []
            for raw in data.get("suggested_values") or []:
                try:
                    value = float(str(raw).replace(",", ""))
                except ValueError:
                    continue
                if low <= value <= high:
                    suggested.append(value)
            knowledge_low = low
            knowledge_high = high
            if self.publication_mode:
                try:
                    knowledge_low = max(low, float(data.get("min_value")))
                    knowledge_high = min(high, float(data.get("max_value")))
                except (TypeError, ValueError):
                    knowledge_low, knowledge_high = low, high
                if not knowledge_low <= default <= knowledge_high:
                    knowledge_low = min(knowledge_low, default)
                    knowledge_high = max(knowledge_high, default)
                if knowledge_low >= knowledge_high:
                    raise RuntimeError(f"{self.scene}/{name}: empty GPTuner refined range")
            anchors = suggested or [default]
            values = {default}
            for anchor in anchors:
                values.update(
                    {
                        anchor,
                        anchor + 0.25 * (knowledge_low - anchor),
                        anchor + 0.25 * (knowledge_high - anchor),
                        anchor + 0.50 * (knowledge_low - anchor),
                        anchor + 0.50 * (knowledge_high - anchor),
                    }
                )
            coarse_values = sorted(
                {
                    float(min(knowledge_high, max(knowledge_low, value)))
                    for value in values
                }
            )
            self.coarse_space.add(
                Categorical(name, items=coarse_values, default=default)
            )
            self.knowledge_summary[name] = {
                "source": str(path),
                "source_available": path.is_file(),
                "suggested_values": suggested,
                "knowledge_min_value": knowledge_low,
                "knowledge_max_value": knowledge_high,
                "coarse_values": coarse_values,
            }

        self.fine_space = ConfigurationSpace(seed=seed)
        for name in self.active_params:
            summary = self.knowledge_summary[name]
            low = float(summary["knowledge_min_value"])
            high = float(summary["knowledge_max_value"])
            self.fine_space.add(
                Float(
                    name,
                    bounds=(low, high),
                    default=self.defaults[name],
                    log=False,
                )
            )

        def unused_target(config: Any, seed: int = 0) -> float:
            _ = config, seed
            return 0.0

        scratch = Path(tempfile.mkdtemp(prefix=f"mysql_{scene}_gptuner_smac_"))
        coarse_scenario = Scenario(
            self.coarse_space,
            name=str(scratch / "coarse"),
            seed=seed,
            deterministic=True,
            n_trials=GPTUNER_COARSE_TRIALS,
            use_default_config=True,
        )
        initial = LatinHypercubeInitialDesign(
            coarse_scenario,
            n_configs=min(6, GPTUNER_COARSE_TRIALS),
            max_ratio=0.8,
        )
        self.coarse_smac = HyperparameterOptimizationFacade(
            coarse_scenario,
            unused_target,
            initial_design=initial,
            overwrite=True,
            logging_level=False,
        )
        fine_scenario = Scenario(
            self.fine_space,
            name=str(scratch / "fine"),
            seed=seed,
            deterministic=True,
            n_trials=max(candidate_budget, GPTUNER_COARSE_TRIALS),
            # The default point is already present in the knowledge-shaped
            # coarse phase.  Injecting it again is both a duplicate and, in
            # SMAC 2.3.x, can leak a Configuration owned by another facade
            # when several heterogeneous spaces coexist in one process.
            use_default_config=False,
        )
        self.fine_smac = HyperparameterOptimizationFacade(
            fine_scenario,
            unused_target,
            overwrite=True,
            logging_level=False,
        )
        self._rehydrate()

    def _rehydrate(self) -> None:
        from ConfigSpace import Configuration

        for observation in self.observations:
            if observation.get("valid") is not True:
                continue
            targets = [(self.fine_space, self.fine_smac)]
            if observation.get("stage") == "coarse":
                targets.insert(0, (self.coarse_space, self.coarse_smac))
            for space, smac in targets:
                config = Configuration(
                    space,
                    values={
                        name: float(observation["params"][name])
                        for name in self.active_params
                    },
                )
                info = self.TrialInfo(config=config, seed=self.seed)
                smac.tell(
                    info,
                    self.TrialValue(
                        cost=float(observation["cost"]),
                        time=float(observation.get("elapsed_s") or 0.0),
                    ),
                )

    def suggest(self, count: int) -> List[Dict[str, Any]]:
        completed = len(self.observations)
        used = {str(row.get("param_signature")) for row in self.observations}
        suggestions: List[Dict[str, Any]] = []

        def take(stage: str, requested: int) -> None:
            smac = self.coarse_smac if stage == "coarse" else self.fine_smac
            stage_start = len(suggestions)
            for _attempt in range(200):
                if len(suggestions) - stage_start >= requested:
                    break
                info = smac.ask()
                raw_params = dict(info.config)
                expected = set(self.active_params)
                actual = set(raw_params)
                if actual != expected:
                    self.incompatible_asks_skipped.append(
                        {
                            "stage": stage,
                            "missing": sorted(expected - actual),
                            "unexpected": sorted(actual - expected),
                        }
                    )
                    continue
                params = {
                    name: float(raw_params[name]) for name in self.active_params
                }
                signature = exact_signature(params)
                if signature in used or signature in self.pending:
                    continue
                used.add(signature)
                self.pending[signature] = (smac, info)
                suggestions.append(
                    {
                        "trial": completed + len(suggestions) + 1,
                        "stage": stage,
                        "params": params,
                        "policy": (
                            "gptuner_official_coarse_to_fine_smac_mysql_v1"
                        ),
                    }
                )
            if len(suggestions) - stage_start != requested:
                raise RuntimeError(
                    f"{self.scene}: GPTuner {stage} distinct suggestion exhaustion"
                )

        coarse_remaining = max(0, GPTUNER_COARSE_TRIALS - completed)
        coarse_count = min(count, coarse_remaining)
        if coarse_count:
            take("coarse", coarse_count)
        fine_count = count - coarse_count
        if fine_count:
            take("fine", fine_count)
        if len(suggestions) != count:
            raise RuntimeError(f"{self.scene}: GPTuner distinct suggestion exhaustion")
        return suggestions

    def observe(
        self,
        suggestion: Mapping[str, Any],
        observation: Mapping[str, Any],
    ) -> None:
        signature = exact_signature(suggestion["params"])
        smac, info = self.pending.pop(signature)
        smac.tell(
            info,
            self.TrialValue(
                cost=float(observation["cost"]),
                time=float(observation.get("elapsed_s") or 0.0),
            ),
        )

    def metadata(self) -> Dict[str, Any]:
        return {
            "contract": "gptuner_online_knowledge_coarse_to_fine_smac_mysql_v2",
            "official_repository": "https://github.com/SolidLao/GPTuner",
            "official_commit": git_commit(self.official_root),
            "coarse_trials": GPTUNER_COARSE_TRIALS,
            "candidate_budget": self.candidate_budget,
            "knowledge_summary": self.knowledge_summary,
            "fine_default_reinjection": False,
            "exact_active_key_gate": True,
            "incompatible_asks_skipped": copy.deepcopy(
                self.incompatible_asks_skipped
            ),
            "online_llm_refresh": self.knowledge_snapshot is not None,
            "llm_model": self.knowledge_manifest.get("model"),
            "llm_provider": self.knowledge_manifest.get("provider"),
            "paper_model": self.knowledge_manifest.get("paper_model"),
            "paper_model_equivalent": self.knowledge_manifest.get(
                "paper_model_equivalent"
            ),
            "model_substitution_explicit": self.allow_model_substitution,
            "llm_call_count": self.knowledge_manifest.get("llm_call_count"),
            "llm_prompt_tokens": self.knowledge_manifest.get("prompt_tokens"),
            "llm_completion_tokens": self.knowledge_manifest.get(
                "completion_tokens"
            ),
            "knowledge_snapshot": (
                str(self.knowledge_snapshot) if self.knowledge_snapshot else None
            ),
            "knowledge_manifest_sha256": (
                hashlib.sha256(
                    (self.knowledge_snapshot / "manifest.json").read_bytes()
                ).hexdigest()
                if self.knowledge_snapshot
                else None
            ),
            "region_discard_from_structured_min_max": self.publication_mode,
            "fine_space_distribution": "linear_uniform",
            "knob_selection_mode": "fixed_common_four_parameter_scene_domain",
            "knob_selection_adaptation_reason": (
                "Both methods must optimize the same predeclared four MySQL "
                "optimizer-cost parameters in each scene."
            ),
            "special_value_policy": (
                "Special values are classified and audited in the knowledge "
                "snapshot but excluded when outside the shared positive numeric domain."
            ),
            "budget_adaptation": {
                "paper_artifact_coarse_trials": 30,
                "paper_artifact_total_trials": 110,
                "comparison_coarse_trials": GPTUNER_COARSE_TRIALS,
                "comparison_total_trials": self.candidate_budget,
                "reason": "equal 35-configuration physical budget",
            },
            "domain_adaptation_disclosed": True,
            "qps_used_only_as_black_box_objective": True,
        }


class AQETunerPolicy:
    """Public AQETuner PSO warm start plus dual-task EIC acquisition."""

    def __init__(
        self,
        *,
        scene: str,
        active_params: Sequence[str],
        ranges: Mapping[str, Tuple[float, float]],
        defaults: Mapping[str, float],
        observations: MutableSequence[Dict[str, Any]],
        candidate_budget: int,
        seed: int,
        official_root: Path,
        plan: Mapping[str, Any],
        output_dir: Path,
        publication_mode: bool = False,
    ) -> None:
        self.scene = scene
        self.active_params = tuple(active_params)
        self.ranges = dict(ranges)
        self.defaults = dict(defaults)
        self.observations = observations
        self.candidate_budget = candidate_budget
        self.seed = seed
        self.official_root = official_root
        self.plan = copy.deepcopy(dict(plan))
        self.output_dir = output_dir
        self.publication_mode = publication_mode
        self.checkpoint_path = output_dir / "aqetuner_model.tar"
        self.sample_path = output_dir / "aqetuner_samples.tsv"
        if str(official_root) not in sys.path:
            sys.path.insert(0, str(official_root))
        os.environ.setdefault("DB", f"mysql_{scene}")
        import torch as torch_module

        self.torch = torch_module
        self.device = "cpu"
        self.rng = np.random.default_rng(seed)
        self._initialize_pso()
        self._rehydrate_pso()
        self.model: Any = None
        self.context_pairs: List[Any] = []
        self.context_labels: List[List[float]] = []

    def normalized_vector(self, params: Mapping[str, float]) -> List[float]:
        result = []
        for name in self.active_params:
            low, high = self.ranges[name]
            value = float(params[name])
            if high / low >= 20.0:
                result.append(
                    (math.log(value) - math.log(low))
                    / (math.log(high) - math.log(low))
                )
            else:
                result.append((value - low) / (high - low))
        return result

    def denormalized_params(self, values: Sequence[float]) -> Dict[str, float]:
        params: Dict[str, float] = {}
        for name, value in zip(self.active_params, values, strict=True):
            low, high = self.ranges[name]
            clipped = min(1.0, max(0.0, float(value)))
            if high / low >= 20.0:
                params[name] = math.exp(
                    math.log(low) + clipped * (math.log(high) - math.log(low))
                )
            else:
                params[name] = low + clipped * (high - low)
        return params

    def _initialize_pso(self) -> None:
        sampler = qmc.Sobol(
            d=len(self.active_params), scramble=True, seed=self.seed
        )
        self.pso_positions = sampler.random_base2(m=2)[:3]
        self.pso_velocities = self.rng.uniform(
            -0.1, 0.1, size=self.pso_positions.shape
        )
        self.pso_local_best_positions = self.pso_positions.copy()
        self.pso_local_best_scores = np.full(3, np.inf)
        self.pso_global_best_position = self.pso_positions[0].copy()
        self.pso_global_best_score = float("inf")

    @staticmethod
    def _observation_latency(observation: Mapping[str, Any]) -> float:
        if observation.get("valid") is not True:
            return 100.0
        objective_score = float(observation.get("objective_score") or 0.0)
        if objective_score > 0.0:
            return 1.0 / objective_score
        qps = float(observation.get("db_qps") or 0.0)
        return 1.0 / qps if qps > 0.0 else 100.0

    def _update_pso(
        self,
        particle_id: int,
        values: np.ndarray,
        observation: Mapping[str, Any],
    ) -> None:
        if observation.get("valid") is not True:
            self.pso_velocities[particle_id] = self.rng.uniform(
                -0.1, 0.1, size=len(self.active_params)
            )
            self.pso_positions[particle_id] = self.rng.random(
                len(self.active_params)
            )
            return
        score = self._observation_latency(observation)
        if score < self.pso_local_best_scores[particle_id]:
            self.pso_local_best_scores[particle_id] = score
            self.pso_local_best_positions[particle_id] = values.copy()
        if score < self.pso_global_best_score:
            self.pso_global_best_score = score
            self.pso_global_best_position = values.copy()
        velocity = self.pso_velocities[particle_id]
        velocity = (
            velocity
            + 0.5
            * float(self.rng.random())
            * (self.pso_local_best_positions[particle_id] - values)
            + 0.5
            * float(self.rng.random())
            * (self.pso_global_best_position - values)
        )
        self.pso_velocities[particle_id] = velocity
        self.pso_positions[particle_id] = np.clip(values + velocity, 0.0, 1.0)

    def _rehydrate_pso(self) -> None:
        for observation in self.observations:
            if observation.get("stage") != "pso_warm_start":
                continue
            particle_id = max(0, int(observation.get("trial") or 0) - 1) % 3
            values = np.asarray(
                self.normalized_vector(observation["params"]), dtype=float
            )
            self._update_pso(particle_id, values, observation)

    def _write_samples_and_train(self) -> None:
        warm = [
            row for row in self.observations[:AQETUNER_WARM_SAMPLES]
            if row.get("valid") is True or row.get("model_failure") is True
        ]
        if len(warm) < 4:
            raise RuntimeError(
                f"{self.scene}: AQETuner needs four valid warm-start observations"
            )
        if len(self.observations) < AQETUNER_WARM_SAMPLES:
            return
        with self.sample_path.open("w", encoding="utf-8") as handle:
            for observation in warm:
                qps = float(observation.get("db_qps") or 0.0)
                fail = 0 if qps > 0.0 else 1
                latency = self._observation_latency(observation)
                handle.write(json.dumps({"plan": self.plan}) + "\t")
                handle.write(json.dumps({"plan": self.plan}) + "\t")
                handle.write(
                    json.dumps(self.normalized_vector(observation["params"]))
                    + "\t"
                )
                handle.write(json.dumps({"elapsed": latency, "fail": fail}) + "\n")
        if not self.checkpoint_path.is_file():
            self._train_official_model()
        self._load_model_context()

    def _train_official_model(self) -> None:
        """Train the public AQETuner model on the policy's frozen device.

        The upstream ``train`` module selects CUDA at import time whenever a
        GPU is visible, while its ``TPair`` feature tensors remain on CPU.
        That mixed placement fails at the first matrix multiplication.  The
        external-baseline adapter intentionally uses CPU for both training and
        inference, so force the upstream module to the same device before
        invoking its unmodified training loop.
        """
        import train as aqe_train

        random.seed(self.seed)
        np.random.seed(self.seed)
        self.torch.manual_seed(self.seed)
        aqe_train.DEVICE = self.device
        aqe_train.main(
            str(self.sample_path),
            str(self.checkpoint_path),
            epochs=5,
            lr=0.0005,
        )

    def _load_model_context(self) -> None:
        if self.model is not None:
            return
        from network import LatentModel
        from pair_encoder import TPair

        checkpoint = self.torch.load(
            self.checkpoint_path,
            map_location=self.device,
            weights_only=False,
        )
        model = LatentModel(32).to(self.device)
        model.load_state_dict(checkpoint["model"])
        model.train(False)
        self.model = model
        for observation in self.observations:
            if (
                observation.get("valid") is not True
                and observation.get("model_failure") is not True
            ):
                continue
            qps = float(observation.get("db_qps") or 0.0)
            self.context_pairs.append(
                TPair(
                    copy.deepcopy(self.plan),
                    self.normalized_vector(observation["params"]),
                )
            )
            self.context_labels.append(
                [
                    self._observation_latency(observation),
                    0.0 if observation.get("valid") is True and qps > 0.0 else 1.0,
                ]
            )

    def suggest(self, count: int) -> List[Dict[str, Any]]:
        completed = len(self.observations)
        if completed < AQETUNER_WARM_SAMPLES:
            count = min(count, AQETUNER_WARM_SAMPLES - completed)
            return [
                {
                    "trial": completed + offset + 1,
                    "stage": "pso_warm_start",
                    "params": self.denormalized_params(
                        self.pso_positions[(completed + offset) % 3]
                    ),
                    "policy": "aqetuner_public_pso_warm_start_mysql_v1",
                    "pso_particle_id": (completed + offset) % 3,
                    "pso_feedback_applied": completed + offset >= 3,
                }
                for offset in range(count)
            ]

        self._write_samples_and_train()
        from pair_encoder import TPair

        sampler = qmc.Sobol(
            d=len(self.active_params),
            scramble=True,
            seed=self.seed + completed,
        )
        values = sampler.random_base2(m=10)
        candidate_pairs = [
            TPair(copy.deepcopy(self.plan), list(row)) for row in values
        ]
        context_y = self.torch.tensor(
            self.context_labels,
            dtype=self.torch.float32,
            device=self.device,
        )
        with self.torch.no_grad():
            latency_dist, failure_dist, _loss = self.model(
                self.context_pairs, context_y, candidate_pairs
            )
        mu = latency_dist.mean.detach().cpu().numpy().reshape(-1)
        failure_probability = (
            failure_dist.mean.detach().cpu().numpy().reshape(-1)
        )
        feasible_latency = mu.copy()
        feasible_latency[failure_probability > 0.5] = 1000.0
        used = {str(row.get("param_signature")) for row in self.observations}
        suggestions: List[Dict[str, Any]] = []
        for index in np.argsort(feasible_latency):
            params = self.denormalized_params(values[int(index)])
            signature = exact_signature(params)
            if signature in used:
                continue
            used.add(signature)
            suggestions.append(
                {
                    "trial": completed + len(suggestions) + 1,
                    "stage": "dual_task_failure_threshold",
                    "params": params,
                    "policy": "aqetuner_public_failure_threshold_mysql_v2",
                    "predicted_latency": float(mu[int(index)]),
                    "predicted_failure_probability": float(
                        failure_probability[int(index)]
                    ),
                    "failure_probability_threshold": 0.5,
                    "acquisition_value": float(feasible_latency[int(index)]),
                }
            )
            if len(suggestions) >= count:
                break
        if len(suggestions) != count:
            raise RuntimeError(f"{self.scene}: AQETuner suggestion exhaustion")
        return suggestions

    def observe(
        self,
        suggestion: Mapping[str, Any],
        observation: Mapping[str, Any],
    ) -> None:
        if suggestion.get("stage") == "pso_warm_start":
            particle_id = int(suggestion.get("pso_particle_id") or 0)
            values = np.asarray(
                self.normalized_vector(observation["params"]), dtype=float
            )
            self._update_pso(particle_id, values, observation)
            return
        if self.model is None:
            return
        if (
            observation.get("valid") is not True
            and observation.get("model_failure") is not True
        ):
            return
        from pair_encoder import TPair

        qps = float(observation.get("db_qps") or 0.0)
        self.context_pairs.append(
            TPair(
                copy.deepcopy(self.plan),
                self.normalized_vector(observation["params"]),
            )
        )
        self.context_labels.append(
            [
                self._observation_latency(observation),
                0.0 if observation.get("valid") is True and qps > 0.0 else 1.0,
            ]
        )

    def metadata(self) -> Dict[str, Any]:
        return {
            "contract": "aqetuner_public_artifact_mysql_cost_port_v2",
            "official_repository": (
                "https://gitfront.io/r/ml4tuning/aoR8ReCrj4a7/aqetuner/"
            ),
            "official_commit": git_commit(self.official_root),
            "candidate_budget": self.candidate_budget,
            "warm_start_samples": AQETUNER_WARM_SAMPLES,
            "warm_start": "three_particle_feedback_updated_pso",
            "pso_feedback_applied_after_each_observation": True,
            "predictor": "public_dual_task_neural_process",
            "acquisition": (
                "minimum_predicted_latency_after_failure_probability_0.5_filter"
            ),
            "training_device": self.device,
            "inference_device": self.device,
            "upstream_cuda_autoselection_overridden": True,
            "plan_context_fixed_per_scene": True,
            "qps_used_only_as_black_box_objective": True,
            "sequential_feedback": self.publication_mode,
            "global_port_limitation": (
                "The public AQETuner artifact tunes one configuration per query. "
                "For equal global-configuration expressiveness, this disclosed "
                "port uses one complete all-query composite plan per MySQL scene."
            ),
            "domain_adaptation_disclosed": True,
        }
