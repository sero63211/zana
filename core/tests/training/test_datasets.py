"""Dataset split isolation, raw-document policy, and synthetic metadata tests."""

from __future__ import annotations

from pathlib import Path

from zana_core.training.contracts import DatasetSplitManifest
from zana_core.training.datasets import (
    check_split_isolation,
    held_out_range_disjoint,
    reject_raw_documents_as_training_targets,
    synthetic_dataset_contract,
)


def _manifest(role: str, digest: str, ids: tuple[str, ...]) -> DatasetSplitManifest:
    return DatasetSplitManifest(
        role=role,
        path=Path(f"/data/{role}.jsonl"),
        sha256=digest,
        size_bytes=1,
        record_ids=ids,
    )


class TestSplitIsolation:
    def test_disjoint_splits_are_ok(self) -> None:
        report = check_split_isolation(
            _manifest("train", "aaa", ("train-1",)),
            _manifest("validation", "bbb", ("val-1",)),
            _manifest("evaluation", "ccc", ("eval-1",)),
        )
        assert report.ok is True

    def test_shared_digest_detected(self) -> None:
        report = check_split_isolation(
            _manifest("train", "aaa", ("train-1",)),
            _manifest("evaluation", "aaa", ("eval-1",)),
        )
        assert report.ok is False
        assert "aaa" in report.shared_file_digests

    def test_shared_record_id_detected(self) -> None:
        report = check_split_isolation(
            _manifest("train", "aaa", ("shared",)),
            _manifest("validation", "bbb", ("shared",)),
        )
        assert report.ok is False
        assert "shared" in report.duplicate_record_ids

    def test_evaluation_never_enters_training(self) -> None:
        report = check_split_isolation(
            _manifest("train", "train-digest", ("train-1",)),
            evaluation=_manifest("evaluation", "eval-digest", ("eval-1",)),
        )
        assert report.ok is True


class TestTrainingTargetPolicy:
    def test_raw_books_rejected(self) -> None:
        result = reject_raw_documents_as_training_targets(
            is_task_oriented=False,
            raw_document_kind="book",
        )
        assert result.allowed is False
        assert "RAG" in result.reason

    def test_task_oriented_allowed(self) -> None:
        result = reject_raw_documents_as_training_targets(is_task_oriented=True)
        assert result.allowed is True

    def test_non_task_oriented_rejected(self) -> None:
        result = reject_raw_documents_as_training_targets(is_task_oriented=False)
        assert result.allowed is False


class TestSyntheticDataset:
    def test_contract_captures_generator_seed_and_heldout(self) -> None:
        dataset = synthetic_dataset_contract(
            generator_identity="math-v1",
            seed=7,
            label_verifier="sympy-verify",
            held_out_seed=99,
            held_out_range=(1000, 2000),
        )
        assert dataset.generator_identity == "math-v1"
        assert dataset.seed == 7
        assert dataset.held_out_seed == 99
        assert dataset.held_out_range == (1000, 2000)

    def test_disjoint_held_out_range(self) -> None:
        dataset = synthetic_dataset_contract(
            generator_identity="math-v1",
            seed=7,
            label_verifier="verify",
            held_out_seed=99,
            held_out_range=(1000, 2000),
        )
        assert held_out_range_disjoint(dataset, train_start=0, train_end=1000) is True
        assert held_out_range_disjoint(dataset, train_start=500, train_end=1500) is False
