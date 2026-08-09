"""Deterministic strategy composition for capability builds."""

from __future__ import annotations

from zana_core.domain.enums import ModelIdentityStrength
from zana_core.planning.models import (
    ADAPTER_ELIGIBLE_TRAINING_GOALS,
    LARGE_KNOWLEDGE_BYTES,
    STRATEGY_COMPONENT_MAP,
    BuildPolicy,
    CapabilityFacts,
    HardwareFacts,
    ModelFacts,
    StrategyComponent,
    StrategyDecision,
    StrategyMode,
    TrainingProviderCompatibility,
    external_tool_ids,
    trusted_tool_ids,
)


def _strong_identity(model: ModelFacts) -> tuple[bool, str]:
    if model.identity_strength != ModelIdentityStrength.EXACT_DIGEST:
        return False, "identity_strength is not exact_digest"
    if model.digest is None:
        return False, "model digest is missing"
    if not model.training_source_identity or not model.adapter_base_identity:
        return False, "training or adapter base identity is missing"
    if model.training_source_identity != model.adapter_base_identity:
        return False, "training_source_identity and adapter_base_identity differ"
    if model.digest != model.training_source_identity:
        return False, "model digest does not match the training source identity"
    if model.runtime_identity not in (None, model.training_source_identity):
        return False, "runtime_identity conflicts with the training source identity"
    return True, "exact digest identity binds training source and adapter base"


def _adapter_eligibility(
    *,
    policy: BuildPolicy,
    capability: CapabilityFacts,
    model: ModelFacts,
    provider: TrainingProviderCompatibility,
    hardware: HardwareFacts,
) -> tuple[bool, list[str], list[str]]:
    """Return (eligible, reasons, blockers). Blockers only apply to explicit choice."""
    reasons: list[str] = []
    blockers: list[str] = []
    if not policy.allow_adapter_training:
        return (
            False,
            ["adapter training is disabled by policy"],
            ["ADAPTER_DISABLED_BY_POLICY: adapter training is disabled by policy"],
        )
    if not capability.has_training:
        return (
            False,
            ["documents are knowledge for RAG; no supervised training dataset is declared"],
            [],
        )
    if not capability.training_files_present:
        reasons.append("declared training files are not present; adapter skipped")
        blockers.append("TRAINING_FILES_MISSING: declared training files are absent")
        return False, reasons, blockers
    example_count = capability.train_record_count or 0
    validation_count = capability.validation_record_count or 0
    if example_count <= 0:
        reasons.append("no supervised training examples; documents alone are not a dataset")
        blockers.append("TRAINING_EXAMPLES_MISSING: no supervised training examples")
        return False, reasons, blockers
    if capability.minimum_examples is not None and example_count < capability.minimum_examples:
        reasons.append(
            f"{example_count} training examples are below the declared "
            f"minimumExamples {capability.minimum_examples}"
        )
        blockers.append("TRAINING_EXAMPLES_INSUFFICIENT: examples are below the declared minimum")
        return False, reasons, blockers
    if capability.training_goal not in ADAPTER_ELIGIBLE_TRAINING_GOALS:
        reasons.append(
            f"training goal {capability.training_goal!r} is not an adapter-eligible "
            "task-oriented goal"
        )
        blockers.append(
            "TRAINING_GOAL_UNSUPPORTED: the declared training goal is not adapter-eligible"
        )
        return False, reasons, blockers
    if validation_count <= 0:
        reasons.append("held-out validation split is missing; adapter skipped")
        blockers.append("TRAINING_VALIDATION_MISSING: validation split is required")
        return False, reasons, blockers
    if not capability.leakage_ok:
        reasons.append("held-out leakage detected; adapter training is blocked")
        blockers.append("TRAINING_LEAKAGE: training and evaluation data overlap")
        return False, reasons, blockers
    if not capability.has_evaluation or not capability.evaluation_domain_records:
        reasons.append("evaluation suite is required for adapter verification")
        blockers.append("ADAPTER_EVALUATION_MISSING: adapter requires evaluation suites")
        return False, reasons, blockers

    strong, identity_reason = _strong_identity(model)
    if not strong:
        reasons.append(
            "exact base/training/adapter identity is not proven; adapter skipped "
            f"({identity_reason})"
        )
        blockers.append(f"MODEL_IDENTITY_WEAK: {identity_reason}; never inferred from display name")
        return False, reasons, blockers
    if not model.training_source_available:
        reasons.append(
            "trainable training source is not available locally; explicit download "
            "approval is required"
        )

    if not provider.supported or not provider.installed:
        reasons.append(f"training provider {provider.provider_id!r} is not supported/installed")
        blockers.append("PROVIDER_UNAVAILABLE: training provider is unavailable")
        return False, reasons, blockers
    if provider.compatible_arch is False:
        reasons.append(
            f"training provider {provider.provider_id!r} is incompatible with this hardware"
        )
        blockers.append("PROVIDER_ARCH_INCOMPATIBLE: provider cannot run on this hardware")
        return False, reasons, blockers
    if provider.provider_id not in hardware.training_backends:
        reasons.append(
            f"training provider {provider.provider_id!r} is not detected on this machine"
        )
        blockers.append("PROVIDER_NOT_DETECTED: provider is not in the hardware profile")
        return False, reasons, blockers

    reasons.append(
        "task-oriented examples with disjoint held-out evaluation and exact "
        "base identity support adapter training"
    )
    return True, reasons, []


def _rag_recommended(capability: CapabilityFacts, policy: BuildPolicy) -> bool:
    if not capability.has_knowledge:
        return False
    training_eligible = (
        capability.train_record_count not in (None, 0)
        and capability.training_goal in ADAPTER_ELIGIBLE_TRAINING_GOALS
    )
    scarce_examples = capability.train_record_count is None or capability.train_record_count < (
        capability.minimum_examples or 0
    )
    if policy.prefer_training and training_eligible:
        scarce_examples = False
    return (
        capability.knowledge_citation_required
        or capability.knowledge_bytes is None
        or capability.knowledge_bytes >= LARGE_KNOWLEDGE_BYTES
        or scarce_examples
    )


def compose_strategy(
    *,
    policy: BuildPolicy,
    capability: CapabilityFacts,
    model: ModelFacts,
    provider: TrainingProviderCompatibility,
    hardware: HardwareFacts,
) -> StrategyDecision:
    """Compose a deterministic strategy; explicit overrides never fall back."""
    reasons: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []
    explicit = (
        STRATEGY_COMPONENT_MAP[policy.strategy] if policy.strategy != StrategyMode.AUTO else None
    )

    if capability.has_tools and not capability.tool_ids:
        warnings.append(
            "tool ids are unknown; the planner cannot include tools until the "
            "tool manifest is resolved"
        )

    selected: list[StrategyComponent] = []

    # RAG
    rag_wanted = explicit is not None and StrategyComponent.RAG in explicit
    if explicit is None:
        rag_wanted = _rag_recommended(capability, policy)
    if rag_wanted and not capability.has_knowledge:
        blockers.append("STRATEGY_INCOMPATIBLE: RAG requested but capability has no knowledge")
    elif rag_wanted:
        selected.append(StrategyComponent.RAG)
        reasons.append(
            "capability declares knowledge; RAG serves factual, updatable, or "
            "citation-required content without memorizing documents"
        )
    elif capability.has_knowledge:
        reasons.append(
            "knowledge is present but training examples are sufficient and no "
            "citation/large-corpus signal requires RAG"
        )

    # Tools
    trusted = trusted_tool_ids(capability) if capability.has_tools else ()
    external = external_tool_ids(capability)
    if external:
        warnings.append("external MCP tools are disabled by default and excluded from the plan")
    tools_wanted = explicit is not None and StrategyComponent.TOOLS in explicit
    if explicit is None:
        tools_wanted = bool(trusted)
    if tools_wanted and not trusted:
        blockers.append(
            "STRATEGY_INCOMPATIBLE: TOOLS requested but no trusted built-in tool is declared"
        )
    elif tools_wanted:
        selected.append(StrategyComponent.TOOLS)
        reasons.append(f"declared tools are trusted built-ins: {', '.join(trusted)}")

    # Adapter
    adapter_wanted = explicit is not None and StrategyComponent.ADAPTER in explicit
    if explicit is None:
        adapter_wanted = True
    eligible, adapter_reasons, adapter_blockers = _adapter_eligibility(
        policy=policy,
        capability=capability,
        model=model,
        provider=provider,
        hardware=hardware,
    )
    if not eligible:
        reasons.extend(adapter_reasons)
        if explicit is not None and adapter_wanted:
            blockers.append(
                "STRATEGY_INCOMPATIBLE: adapter requested but not feasible: "
                + "; ".join(dict.fromkeys(adapter_reasons))
            )
            blockers.extend(adapter_blockers)
        elif adapter_blockers and not capability.training_optional:
            blockers.extend(adapter_blockers)
        elif adapter_blockers:
            warnings.extend("AUTO_ADAPTER_SKIPPED: " + blocker for blocker in adapter_blockers)
    else:
        if adapter_wanted:
            selected.append(StrategyComponent.ADAPTER)
            reasons.extend(adapter_reasons)
        else:
            reasons.append("adapter training is not preferred by the current policy")

    if explicit is not None and StrategyComponent.ADAPTER not in explicit:
        reasons.append("adapter was excluded by the explicit strategy override")

    components = tuple(selected)
    strategy_id = "+".join(component.value for component in components) or "none"
    return StrategyDecision(
        components=components,
        strategy_id=strategy_id,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        blockers=tuple(blockers),
    )
