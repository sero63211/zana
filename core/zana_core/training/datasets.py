"""Dataset split disjointness, task-oriented policy, and synthetic metadata."""

from __future__ import annotations

from dataclasses import dataclass

from zana_core.training.contracts import DatasetSplitManifest, SyntheticDataset


@dataclass(frozen=True, slots=True)
class DatasetIsolation:
    """Report shared file digests or record ids across splits."""

    shared_file_digests: tuple[str, ...]
    duplicate_record_ids: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.shared_file_digests and not self.duplicate_record_ids


def check_split_isolation(
    train: DatasetSplitManifest,
    validation: DatasetSplitManifest | None = None,
    evaluation: DatasetSplitManifest | None = None,
) -> DatasetIsolation:
    """Require train/validation/evaluation disjointness by digest and ids."""
    manifests = [train]
    if validation is not None:
        manifests.append(validation)
    if evaluation is not None:
        manifests.append(evaluation)
    digest_counts: dict[str, int] = {}
    id_counts: dict[str, int] = {}
    for manifest in manifests:
        digest_counts[manifest.sha256] = digest_counts.get(manifest.sha256, 0) + 1
        for record_id in manifest.record_ids:
            id_counts[record_id] = id_counts.get(record_id, 0) + 1
    shared = tuple(sorted(digest for digest, count in digest_counts.items() if count > 1))
    duplicates = tuple(sorted(record_id for record_id, count in id_counts.items() if count > 1))
    return DatasetIsolation(shared_file_digests=shared, duplicate_record_ids=duplicates)


@dataclass(frozen=True, slots=True)
class TrainingTargetPolicy:
    """Policy result for a proposed training target."""

    allowed: bool
    reason: str


def reject_raw_documents_as_training_targets(
    *,
    is_task_oriented: bool,
    raw_document_kind: str | None = None,
) -> TrainingTargetPolicy:
    """Reject raw documents/books; allow only task-oriented supervised targets."""
    if raw_document_kind is not None:
        return TrainingTargetPolicy(
            allowed=False,
            reason=f"raw {raw_document_kind} is not a task-oriented training target; use RAG",
        )
    if not is_task_oriented:
        return TrainingTargetPolicy(
            allowed=False,
            reason="training target must be explicit task-oriented examples",
        )
    return TrainingTargetPolicy(allowed=True, reason="task-oriented supervised target")


def synthetic_dataset_contract(
    *,
    generator_identity: str,
    seed: int,
    label_verifier: str,
    held_out_seed: int,
    held_out_range: tuple[int, int],
) -> SyntheticDataset:
    """Build immutable deterministic synthetic metadata with disjoint held-out range."""
    return SyntheticDataset(
        generator_identity=generator_identity,
        seed=seed,
        label_verifier=label_verifier,
        held_out_seed=held_out_seed,
        held_out_range=held_out_range,
    )


def held_out_range_disjoint(
    dataset: SyntheticDataset,
    *,
    train_start: int,
    train_end: int,
) -> bool:
    """Check that the synthetic held-out range never overlaps the train range."""
    held_start, held_end = dataset.held_out_range
    return held_end <= train_start or held_start >= train_end
