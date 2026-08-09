"""BuildPlanner orchestration producing one immutable BuildPlan."""

from __future__ import annotations

from zana_core.planning.estimates import check_resources, estimate_resources
from zana_core.planning.lifecycle import build_lifecycle
from zana_core.planning.models import (
    AcquisitionMode,
    ApprovalKind,
    ApprovalRequirement,
    ApprovalSet,
    BuildPlan,
    BuildPolicy,
    CapabilityFacts,
    DownloadMode,
    EvaluationFacts,
    HardwareFacts,
    ModelFacts,
    StrategyComponent,
    StrategyDecision,
    TrainingProviderCompatibility,
    build_digest,
)
from zana_core.planning.strategy import compose_strategy


class BuildPlanner:
    """Deterministic non-executing planner: identical inputs, identical plan."""

    def plan(
        self,
        *,
        policy: BuildPolicy,
        model: ModelFacts,
        capability: CapabilityFacts,
        evaluation: EvaluationFacts,
        provider: TrainingProviderCompatibility,
        hardware: HardwareFacts,
        approvals: ApprovalSet | None = None,
    ) -> BuildPlan:
        blockers: list[str] = []
        warnings: list[str] = []

        # Model availability and compatibility gate the whole plan.
        if not model.runtime_online:
            blockers.append("MODEL_RUNTIME_UNAVAILABLE: model runtime is offline")
        if (
            capability.required_context_tokens is not None
            and model.context_length is not None
            and model.context_length < capability.required_context_tokens
        ):
            blockers.append(
                "MODEL_CONTEXT_INSUFFICIENT: model context is below the capability minimum"
            )
        missing_capabilities = sorted(
            set(capability.required_model_capabilities) - set(model.capabilities)
        )
        if missing_capabilities:
            blockers.append(
                "MODEL_CAPABILITY_MISSING: model lacks required capabilities: "
                + ", ".join(missing_capabilities)
            )

        if policy.require_verification and not (evaluation.has_domain or evaluation.has_regression):
            blockers.append(
                "VERIFICATION_REQUIRES_EVALUATION: verification needs evaluation suites"
            )
        if not evaluation.held_out_ok:
            warnings.append(
                "evaluation/training separation is unverified; adapter eligibility "
                "requires an explicit leakage-ok signal"
            )

        strategy = compose_strategy(
            policy=policy,
            capability=capability,
            model=model,
            provider=provider,
            hardware=hardware,
        )
        blockers.extend(strategy.blockers)
        warnings.extend(strategy.warnings)

        estimate = estimate_resources(
            policy=policy,
            model=model,
            capability=capability,
            strategy=strategy,
            provider=provider,
            hardware=hardware,
        )
        resource_check = check_resources(
            policy=policy,
            hardware=hardware,
            estimate=estimate,
        )
        blockers.extend(resource_check.blockers)
        warnings.extend(resource_check.warnings)

        approval_requirements, approval_blockers = self._approval_requirements(
            policy=policy,
            model=model,
            strategy=strategy,
        )
        blockers.extend(approval_blockers)
        if approvals is None:
            approval_set = ApprovalSet.none_granted(approval_requirements)
        else:
            granted = {
                requirement.kind for requirement in approvals.requirements if requirement.granted
            }
            approval_set = ApprovalSet.with_grants(approval_requirements, granted)

        lifecycle = build_lifecycle(
            policy=policy,
            capability=capability,
            evaluation=evaluation,
            strategy=strategy,
            model=model,
        )

        unique_blockers = tuple(dict.fromkeys(blockers))
        unique_warnings = tuple(dict.fromkeys(warnings))
        approvable = not unique_blockers and approval_set.all_granted
        plan = BuildPlan(
            policy=policy,
            model=model,
            capability=capability,
            evaluation=evaluation,
            provider=provider,
            hardware=hardware,
            strategy=strategy,
            resource_estimate=estimate,
            resource_check=resource_check,
            approvals=approval_set,
            lifecycle=lifecycle,
            blockers=unique_blockers,
            warnings=unique_warnings,
            approvable=approvable,
            digest="",
        )
        return plan.model_copy(update={"digest": build_digest(plan)})

    @staticmethod
    def _approval_requirements(
        *,
        policy: BuildPolicy,
        model: ModelFacts,
        strategy: StrategyDecision,
    ) -> tuple[tuple[ApprovalRequirement, ...], list[str]]:
        requirements: list[ApprovalRequirement] = [
            ApprovalRequirement(
                kind=ApprovalKind.PERMISSIONS,
                description="Approve the capability's default-deny permission policy.",
            ),
            ApprovalRequirement(
                kind=ApprovalKind.DISK,
                description="Approve the estimated disk usage and safety reserve.",
            ),
        ]
        blockers: list[str] = []
        has_adapter = StrategyComponent.ADAPTER in strategy.components
        if has_adapter:
            requirements.append(
                ApprovalRequirement(
                    kind=ApprovalKind.TRAINING,
                    description="Approve adapter training against the exact base identity.",
                )
            )
        downloads_needed = has_adapter and not model.training_source_available
        if downloads_needed:
            if policy.acquisition == AcquisitionMode.OFFLINE:
                blockers.append(
                    "NETWORK_OFFLINE_DOWNLOAD: adapter training needs a checkpoint "
                    "download but acquisition is offline"
                )
            elif policy.allow_external_artifact_downloads == DownloadMode.OFFLINE:
                blockers.append(
                    "DOWNLOAD_DENIED: external artifact downloads are disabled by policy"
                )
            elif policy.allow_external_artifact_downloads == DownloadMode.ALLOWED:
                requirements.append(
                    ApprovalRequirement(
                        kind=ApprovalKind.DOWNLOAD,
                        description="Download the trainable base checkpoint.",
                        required=False,
                        reason="downloads are explicitly allowed by policy",
                    )
                )
            else:
                requirements.append(
                    ApprovalRequirement(
                        kind=ApprovalKind.DOWNLOAD,
                        description="Approve downloading the trainable base checkpoint.",
                    )
                )
        return tuple(requirements), blockers
