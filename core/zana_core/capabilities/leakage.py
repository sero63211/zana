"""Train/validation/evaluation separation checks to prevent held-out leakage."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from zana_core.capabilities.evaluation import EvaluationSet
from zana_core.capabilities.training import TrainingSet


@dataclass(frozen=True, slots=True)
class LeakageReport:
    """Immutable summary of cross-split file and record-id overlaps."""

    shared_files: tuple[tuple[str, tuple[str, ...]], ...]
    duplicate_ids: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def ok(self) -> bool:
        return not self.shared_files and not self.duplicate_ids


def _identity(path: Path) -> tuple[int, int]:
    stat_result = os.stat(path, follow_symlinks=True)
    return stat_result.st_dev, stat_result.st_ino


def _iter_loaded(
    train: TrainingSet | None,
    validation: TrainingSet | None,
    domain: EvaluationSet | None,
    regression: EvaluationSet | None,
) -> Iterable[tuple[str, str, Path, Iterable[str]]]:
    if train is not None:
        yield "train", train.file, train.path, (record.id for record in train.records)
    if validation is not None:
        yield (
            "validation",
            validation.file,
            validation.path,
            (record.id for record in validation.records),
        )
    if domain is not None:
        yield "domain", domain.file, domain.path, (record.id for record in domain.records)
    if regression is not None:
        yield (
            "regression",
            regression.file,
            regression.path,
            (record.id for record in regression.records),
        )


def check_leakage(
    declared_files: Iterable[tuple[str, str, Path]],
    train: TrainingSet | None,
    validation: TrainingSet | None,
    domain: EvaluationSet | None,
    regression: EvaluationSet | None,
    *,
    allow_test_overrides: bool = False,
) -> LeakageReport:
    """Detect files or record ids reused across train/validation/evaluation splits.

    ``allow_test_overrides`` is a test-only escape hatch for exercising the
    guard itself; it relaxes only the shared-file rule, never record identity.
    """
    file_owners: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for role, label, path in declared_files:
        identity = _identity(path)
        file_owners.setdefault(identity, []).append((role, label))
    id_owners: dict[str, list[tuple[str, str]]] = {}
    for role, label, _path, records in _iter_loaded(train, validation, domain, regression):
        for record_id in records:
            id_owners.setdefault(record_id, []).append((role, label))

    shared_files: list[tuple[str, tuple[str, ...]]] = []
    for owners in file_owners.values():
        if len(owners) > 1 and not allow_test_overrides:
            label = owners[0][1]
            roles = tuple(sorted(role for role, _ in owners))
            shared_files.append((label, roles))
    duplicate_ids: list[tuple[str, tuple[str, ...]]] = []
    for record_id, owners in sorted(id_owners.items()):
        if len(owners) > 1:
            labels = tuple(sorted(label for _, label in owners))
            duplicate_ids.append((record_id, labels))
    return LeakageReport(
        shared_files=tuple(sorted(shared_files)),
        duplicate_ids=tuple(duplicate_ids),
    )
