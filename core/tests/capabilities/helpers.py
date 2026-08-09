"""Shared fixtures and package builders for capability validation tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from zana_core.capabilities.validator import CapabilitySourceValidator

FIXED_INGESTED_AT = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)

MATH_MANIFEST = """\
schemaVersion: 1
kind: ZanaCapability
id: io.zana.demo.math
name: ZANA Math Demo
version: 0.1.0
description: Deterministic math demo for adapter/tool/evaluation pipeline.
license: MIT
goal:
  type: domain-assistant
  primaryMetrics: [math_exact_accuracy]
training:
  optional: true
  goal: structured_reasoning
  train: training/train.jsonl
  validation: training/valid.jsonl
  minimumExamples: 100
tools:
  manifest: tools/tools.yaml
permissions:
  policy: permissions/policy.yaml
evaluation:
  domain: evals/domain.jsonl
verification:
  gates:
    domain:
      minimumAbsolute: 0.70
      minimumImprovement: 0.05
"""

POLICY_MANIFEST = """\
schemaVersion: 1
kind: ZanaCapability
id: io.zana.demo.policy
name: ZANA Internal Policy Demo
version: 0.1.0
description: Offline RAG assistant over synthetic internal policies.
license: MIT
goal:
  type: domain-assistant
  primaryMetrics: [grounded_qa]
behavior:
  system: behavior/system.md
knowledge:
  sources:
    - path: knowledge/sources
  citationRequired: true
training:
  optional: false
permissions:
  policy: permissions/policy.yaml
evaluation:
  domain: evals/domain.jsonl
verification:
  gates:
    domain:
      minimumAbsolute: 0.80
"""

TOOLS_YAML = """\
tools:
  - id: calculator
    provider: zana.builtin
    version: 1
"""

POLICY_YAML = """\
network:
  outbound: false
filesystem:
  read: []
  write: []
tools:
  allow:
    - calculator
secrets:
  allow: []
"""

MATH_EVAL_JSONL = (
    '{"id":"math-001","prompt":"What is 17 * 23? Return only the number.",'
    '"scorer":{"type":"numeric_exact","expected":391}}\n'
    '{"id":"math-002","prompt":"What is 144 / 12? Return only the number.",'
    '"scorer":{"type":"numeric_exact","expected":12}}\n'
)

POLICY_EVAL_JSONL = (
    '{"id":"policy-001","prompt":"How many remote work days per week can an '
    'eligible employee request?",'
    '"scorer":{"type":"contains_all","expected":["two","Remote Work Policy"]}}\n'
    '{"id":"policy-002","prompt":"When is a receipt required for an individual '
    'travel expense?",'
    '"scorer":{"type":"contains_all","expected":["25 EUR","Travel Expense Policy"]}}\n'
    '{"id":"policy-003","prompt":"What is the company\'s parental leave '
    'duration?","scorer":{"type":"contains_all","expected":["not '
    'contained"]}}\n'
)

POLICY_BEHAVIOR = (
    "You are an internal policy assistant. Answer from provided evidence. Cite the "
    "policy and section used. If the available evidence does not support an answer, "
    "say that the answer is not contained in the available policy sources.\n"
)

MINIMAL_EVAL_JSONL = '{"id":"e","prompt":"p","scorer":{"type":"numeric_exact","expected":1}}\n'

REMOTE_WORK_POLICY = """\
# Remote Work Policy

## 1. Eligibility
Employees who have completed their probationary period may request up to two
remote work days per week.

## 2. Approval
Remote work requires written approval from the employee's manager.
"""

TRAVEL_EXPENSE_POLICY = """\
# Travel Expense Policy

## 1. Receipts
Receipts are required for individual expenses above 25 EUR.

## 2. Submission
Expense reports must be submitted within 30 calendar days after travel ends.
"""

TRAIN_JSONL = (
    '{"id":"train-001","messages":[{"role":"user","content":"Compute 17 * 23 and '
    'explain briefly."},{"role":"assistant","content":"391."}],'
    '"provenance":{"type":"deterministic-generator","generator":"math-v1"}}\n'
    '{"id":"train-002","messages":[{"role":"user","content":"Compute 144 / 12 and '
    'explain briefly."},{"role":"assistant","content":"12."}],'
    '"provenance":{"type":"deterministic-generator","generator":"math-v1"}}\n'
)

VALID_JSONL = (
    '{"id":"valid-001","messages":[{"role":"user","content":"Compute 7 * 8."},'
    '{"role":"assistant","content":"56."}]}\n'
)


def write(root: Path, rel: str, content: str | bytes) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        target.write_text(content, encoding="utf-8")
    else:
        target.write_bytes(content)
    return target


def make_validator(*, allow_test_overrides: bool = False) -> CapabilitySourceValidator:
    return CapabilitySourceValidator(
        ingested_at=FIXED_INGESTED_AT,
        allow_test_overrides=allow_test_overrides,
    )


def build_math_example(root: Path) -> Path:
    write(root, "zana.yaml", MATH_MANIFEST)
    write(root, "tools/tools.yaml", TOOLS_YAML)
    write(root, "permissions/policy.yaml", POLICY_YAML)
    write(root, "evals/domain.jsonl", MATH_EVAL_JSONL)
    return root


def build_policy_example(root: Path) -> Path:
    write(root, "zana.yaml", POLICY_MANIFEST)
    write(root, "behavior/system.md", POLICY_BEHAVIOR)
    write(root, "knowledge/sources/Remote Work Policy.md", REMOTE_WORK_POLICY)
    write(root, "knowledge/sources/Travel Expense Policy.md", TRAVEL_EXPENSE_POLICY)
    write(root, "permissions/policy.yaml", POLICY_YAML)
    write(root, "evals/domain.jsonl", POLICY_EVAL_JSONL)
    return root


def training_manifest(
    *,
    minimum_examples: int = 2,
    train: str = "training/train.jsonl",
    validation: str = "training/valid.jsonl",
) -> str:
    return f"""\
schemaVersion: 1
kind: ZanaCapability
id: io.zana.demo.train
name: Training Demo
version: 0.1.0
license: MIT
goal:
  type: domain-assistant
training:
  optional: false
  train: {train}
  validation: {validation}
  minimumExamples: {minimum_examples}
evaluation:
  domain: evals/domain.jsonl
"""


def build_training_package(
    root: Path,
    *,
    train_jsonl: str = TRAIN_JSONL,
    valid_jsonl: str = VALID_JSONL,
    eval_jsonl: str = MATH_EVAL_JSONL,
    minimum_examples: int = 2,
    train: str = "training/train.jsonl",
    validation: str = "training/valid.jsonl",
) -> Path:
    write(
        root,
        "zana.yaml",
        training_manifest(
            minimum_examples=minimum_examples,
            train=train,
            validation=validation,
        ),
    )
    write(root, train, train_jsonl)
    write(root, validation, valid_jsonl)
    write(root, "evals/domain.jsonl", eval_jsonl)
    return root


def eval_manifest(*, regression: bool = False) -> str:
    regression_block = "  regression: evals/regression.jsonl\n" if regression else ""
    return f"""\
schemaVersion: 1
kind: ZanaCapability
id: io.zana.demo.eval
name: Eval Demo
version: 0.1.0
license: MIT
goal:
  type: domain-assistant
evaluation:
  domain: evals/domain.jsonl
{regression_block}"""


def build_eval_package(
    root: Path,
    *,
    domain_jsonl: str = MATH_EVAL_JSONL,
    regression_jsonl: str | None = None,
) -> Path:
    write(root, "zana.yaml", eval_manifest(regression=regression_jsonl is not None))
    write(root, "evals/domain.jsonl", domain_jsonl)
    if regression_jsonl is not None:
        write(root, "evals/regression.jsonl", regression_jsonl)
    return root
