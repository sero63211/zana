"""Canonical Capability Source validation orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from zana_core.capabilities.behavior import BehaviorSource, load_behavior
from zana_core.capabilities.errors import (
    CapabilityIssue,
    CapabilitySourceValidationError,
    relative_label,
)
from zana_core.capabilities.evaluation import EvalKind, EvaluationSet, load_evaluation_set
from zana_core.capabilities.leakage import LeakageReport, check_leakage
from zana_core.capabilities.manifest import (
    SUPPORTED_KIND,
    SUPPORTED_SCHEMA_VERSION,
    CapabilityManifest,
    DuplicateKeyError,
    parse_safe_yaml,
)
from zana_core.capabilities.paths import (
    PathResolutionError,
    is_prohibited_executable,
    resolve_project_path,
    scan_package_files,
)
from zana_core.capabilities.provenance import (
    SourceProvenance,
    SourceRole,
    make_provenance,
    sha256_of,
)
from zana_core.capabilities.training import TrainingRole, TrainingSet, load_training_set


@dataclass(frozen=True, slots=True)
class TrainingCollections:
    train: TrainingSet | None = None
    validation: TrainingSet | None = None


@dataclass(frozen=True, slots=True)
class EvaluationCollections:
    domain: EvaluationSet | None = None
    regression: EvaluationSet | None = None


@dataclass(frozen=True, slots=True)
class CapabilitySourceValidation:
    """Immutable validation result for one editable capability package."""

    root: Path
    manifest: CapabilityManifest
    behavior: BehaviorSource | None
    training: TrainingCollections
    evaluation: EvaluationCollections
    leakage: LeakageReport
    provenance: tuple[SourceProvenance, ...]


class CapabilitySourceValidator:
    """Validate a complete editable capability package without executing it."""

    def __init__(
        self,
        *,
        ingested_at: datetime | None = None,
        allow_test_overrides: bool = False,
    ) -> None:
        self._ingested_at = ingested_at if ingested_at is not None else datetime.now(UTC)
        self._allow_test_overrides = allow_test_overrides

    def validate(self, root: str | Path) -> CapabilitySourceValidation:
        """Validate ``root`` and return an immutable result or raise with all issues."""
        root_path = Path(root)
        if not root_path.is_dir():
            raise CapabilitySourceValidationError(
                [
                    CapabilityIssue(
                        "ROOT_NOT_FOUND",
                        f"capability root {root_path} is not a readable directory",
                    )
                ]
            )
        root_path = root_path.resolve()
        issues: list[CapabilityIssue] = []

        manifest = self._load_manifest(root_path, issues)
        exact_roles: dict[Path, SourceRole] = {}
        directory_roles: dict[Path, SourceRole] = {}
        resolved_declared: dict[str, Path] = {}
        if manifest is not None:
            exact_roles, directory_roles, resolved_declared = self._resolve_declared_paths(
                root_path, manifest, issues
            )

        package_files = scan_package_files(root_path, issues)
        self._check_executable_content(root_path, package_files, issues)
        self._check_auxiliary_yaml(root_path, exact_roles, issues)

        behavior = None
        behavior_path = self._path_for_role(exact_roles, SourceRole.BEHAVIOR)
        if behavior_path is not None:
            try:
                behavior = load_behavior(root_path, behavior_path)
            except CapabilitySourceValidationError as exc:
                issues.extend(exc.issues)

        training = TrainingCollections()
        if manifest is not None and manifest.training is not None:
            training = self._load_training(root_path, manifest, resolved_declared, issues)

        evaluation = EvaluationCollections()
        if manifest is not None and manifest.evaluation is not None:
            evaluation = self._load_evaluation(root_path, resolved_declared, issues)

        leakage = LeakageReport((), ())
        if manifest is not None:
            declared_files: list[tuple[str, str, Path]] = []
            for key, role in (
                ("training.train", "train"),
                ("training.validation", "validation"),
                ("evaluation.domain", "domain"),
                ("evaluation.regression", "regression"),
            ):
                declared_path = resolved_declared.get(key)
                if declared_path is not None:
                    label = relative_label(root_path, declared_path)
                    declared_files.append((role, label, declared_path))
            leakage = check_leakage(
                declared_files,
                training.train,
                training.validation,
                evaluation.domain,
                evaluation.regression,
                allow_test_overrides=self._allow_test_overrides,
            )
            for label, roles in leakage.shared_files:
                issues.append(
                    CapabilityIssue(
                        "LEAKAGE_SHARED_FILE",
                        f"file {label!r} is reused across splits: {', '.join(roles)}",
                        label,
                    )
                )
            for record_id, labels in leakage.duplicate_ids:
                issues.append(
                    CapabilityIssue(
                        "LEAKAGE_DUPLICATE_ID",
                        f"record id {record_id!r} appears in multiple splits: {', '.join(labels)}",
                    )
                )

        provenance = self._build_provenance(
            root_path,
            package_files,
            exact_roles,
            directory_roles,
            manifest,
            issues,
        )
        if manifest is None:
            raise CapabilitySourceValidationError(issues)
        if issues:
            raise CapabilitySourceValidationError(issues)
        return CapabilitySourceValidation(
            root=root_path,
            manifest=manifest,
            behavior=behavior,
            training=training,
            evaluation=evaluation,
            leakage=leakage,
            provenance=provenance,
        )

    def _load_manifest(
        self, root: Path, issues: list[CapabilityIssue]
    ) -> CapabilityManifest | None:
        path = root / "zana.yaml"
        if not path.is_file():
            issues.append(
                CapabilityIssue(
                    "MANIFEST_MISSING",
                    "zana.yaml is required at the capability root",
                    "zana.yaml",
                )
            )
            return None
        try:
            raw = path.read_bytes()
        except OSError as exc:
            issues.append(
                CapabilityIssue("MANIFEST_READ", f"cannot read zana.yaml: {exc}", "zana.yaml")
            )
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            issues.append(
                CapabilityIssue("MANIFEST_UTF8", "zana.yaml is not valid UTF-8", "zana.yaml")
            )
            return None
        try:
            documents = parse_safe_yaml(text)
        except DuplicateKeyError as exc:
            issues.append(
                CapabilityIssue(
                    "MANIFEST_DUPLICATE_KEY",
                    f"duplicate key {exc.key!r}",
                    "zana.yaml",
                    exc.line,
                )
            )
            return None
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            line = mark.line + 1 if mark is not None and mark.line is not None else None
            issues.append(
                CapabilityIssue("MANIFEST_PARSE", f"invalid YAML: {exc}", "zana.yaml", line)
            )
            return None
        if len(documents) != 1:
            issues.append(
                CapabilityIssue(
                    "MANIFEST_PARSE",
                    "expected exactly one YAML document in zana.yaml",
                    "zana.yaml",
                )
            )
            return None
        data = documents[0]
        if not isinstance(data, dict):
            issues.append(
                CapabilityIssue(
                    "MANIFEST_PARSE",
                    "zana.yaml must contain a mapping",
                    "zana.yaml",
                )
            )
            return None
        if data.get("schemaVersion") != SUPPORTED_SCHEMA_VERSION:
            issues.append(
                CapabilityIssue(
                    "UNSUPPORTED_SCHEMA",
                    f"schemaVersion {data.get('schemaVersion')!r} is not supported; "
                    f"expected {SUPPORTED_SCHEMA_VERSION}",
                    "zana.yaml",
                )
            )
            return None
        if data.get("kind") != SUPPORTED_KIND:
            issues.append(
                CapabilityIssue(
                    "UNSUPPORTED_KIND",
                    f"kind {data.get('kind')!r} is not supported; expected {SUPPORTED_KIND!r}",
                    "zana.yaml",
                )
            )
            return None
        try:
            return CapabilityManifest.model_validate(data)
        except ValidationError as exc:
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"]) or "<root>"
                issues.append(
                    CapabilityIssue(
                        "MANIFEST_INVALID",
                        f"{location}: {error['msg']}",
                        "zana.yaml",
                    )
                )
            return None

    def _resolve_declared_paths(
        self,
        root: Path,
        manifest: CapabilityManifest,
        issues: list[CapabilityIssue],
    ) -> tuple[dict[Path, SourceRole], dict[Path, SourceRole], dict[str, Path]]:
        exact: dict[Path, SourceRole] = {}
        directories: dict[Path, SourceRole] = {}
        resolved_declared: dict[str, Path] = {}

        def resolve(
            ref: str | None,
            role: SourceRole,
            key: str,
            *,
            allow_directory: bool = False,
        ) -> None:
            if ref is None:
                return
            try:
                path = resolve_project_path(root, ref, allow_directory=allow_directory)
            except PathResolutionError as exc:
                optional_training = (
                    role in (SourceRole.TRAINING, SourceRole.VALIDATION)
                    and manifest.training is not None
                    and manifest.training.optional
                    and exc.issue.code == "PATH_NOT_FOUND"
                )
                if optional_training:
                    return
                issues.append(CapabilityIssue(exc.issue.code, exc.issue.message, file=key))
                return
            if allow_directory and path.is_dir():
                directories[path] = role
            else:
                exact[path] = role
            resolved_declared[key] = path

        if manifest.behavior is not None:
            resolve(manifest.behavior.system, SourceRole.BEHAVIOR, "behavior.system")
        if manifest.knowledge is not None and manifest.knowledge.sources:
            for index, source in enumerate(manifest.knowledge.sources):
                resolve(
                    source.path,
                    SourceRole.KNOWLEDGE,
                    f"knowledge.sources[{index}].path",
                    allow_directory=True,
                )
        if manifest.training is not None:
            resolve(manifest.training.train, SourceRole.TRAINING, "training.train")
            resolve(
                manifest.training.validation,
                SourceRole.VALIDATION,
                "training.validation",
            )
        if manifest.tools is not None:
            resolve(manifest.tools.manifest, SourceRole.TOOLS, "tools.manifest")
        if manifest.permissions is not None:
            resolve(manifest.permissions.policy, SourceRole.PERMISSIONS, "permissions.policy")
        if manifest.evaluation is not None:
            resolve(
                manifest.evaluation.domain,
                SourceRole.EVALUATION,
                "evaluation.domain",
            )
            resolve(
                manifest.evaluation.regression,
                SourceRole.EVALUATION,
                "evaluation.regression",
            )
        return exact, directories, resolved_declared

    def _load_training(
        self,
        root: Path,
        manifest: CapabilityManifest,
        resolved_declared: dict[str, Path],
        issues: list[CapabilityIssue],
    ) -> TrainingCollections:
        train_path = resolved_declared.get("training.train")
        validation_path = resolved_declared.get("training.validation")
        train = None
        validation = None
        if train_path is not None:
            try:
                train = load_training_set(root, train_path, TrainingRole.TRAIN)
            except CapabilitySourceValidationError as exc:
                issues.extend(exc.issues)
        if validation_path is not None:
            try:
                validation = load_training_set(root, validation_path, TrainingRole.VALIDATION)
            except CapabilitySourceValidationError as exc:
                issues.extend(exc.issues)
        if (
            manifest.training is not None
            and manifest.training.minimumExamples is not None
            and train is not None
            and len(train.records) < manifest.training.minimumExamples
        ):
            issues.append(
                CapabilityIssue(
                    "TRAINING_MIN_EXAMPLES",
                    f"training set has {len(train.records)} records, below declared "
                    f"minimumExamples {manifest.training.minimumExamples}",
                    train.file,
                )
            )
        return TrainingCollections(train=train, validation=validation)

    def _load_evaluation(
        self,
        root: Path,
        resolved_declared: dict[str, Path],
        issues: list[CapabilityIssue],
    ) -> EvaluationCollections:
        domain_path = resolved_declared.get("evaluation.domain")
        regression_path = resolved_declared.get("evaluation.regression")
        domain = None
        regression = None
        if domain_path is not None:
            try:
                domain = load_evaluation_set(root, domain_path, EvalKind.DOMAIN)
            except CapabilitySourceValidationError as exc:
                issues.extend(exc.issues)
        if regression_path is not None:
            try:
                regression = load_evaluation_set(root, regression_path, EvalKind.REGRESSION)
            except CapabilitySourceValidationError as exc:
                issues.extend(exc.issues)
        return EvaluationCollections(domain=domain, regression=regression)

    @staticmethod
    def _path_for_role(
        exact_roles: dict[Path, SourceRole], role: SourceRole, *, index: int = 0
    ) -> Path | None:
        matches = [path for path, assigned in exact_roles.items() if assigned == role]
        if len(matches) <= index:
            return None
        return matches[index]

    def _check_executable_content(
        self, root: Path, files: list[Path], issues: list[CapabilityIssue]
    ) -> None:
        for path in files:
            prohibited, reason = is_prohibited_executable(path)
            if prohibited:
                issues.append(
                    CapabilityIssue(
                        "HOOK_PROHIBITED",
                        f"executable/hook content is not allowed in capability sources: {reason}",
                        relative_label(root, path),
                    )
                )

    def _check_auxiliary_yaml(
        self,
        root: Path,
        exact_roles: dict[Path, SourceRole],
        issues: list[CapabilityIssue],
    ) -> None:
        for role in (SourceRole.TOOLS, SourceRole.PERMISSIONS):
            path = self._path_for_role(exact_roles, role)
            if path is None:
                continue
            label = relative_label(root, path)
            try:
                raw = path.read_bytes()
            except OSError as exc:
                issues.append(
                    CapabilityIssue("AUXILIARY_READ", f"cannot read auxiliary YAML: {exc}", label)
                )
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                issues.append(
                    CapabilityIssue("AUXILIARY_UTF8", "auxiliary YAML is not valid UTF-8", label)
                )
                continue
            try:
                documents = parse_safe_yaml(text)
            except DuplicateKeyError as exc:
                issues.append(
                    CapabilityIssue(
                        "AUXILIARY_DUPLICATE_KEY",
                        f"duplicate key {exc.key!r}",
                        label,
                        exc.line,
                    )
                )
                continue
            except yaml.YAMLError as exc:
                mark = getattr(exc, "problem_mark", None)
                line = mark.line + 1 if mark is not None and mark.line is not None else None
                issues.append(
                    CapabilityIssue("AUXILIARY_PARSE", f"invalid YAML: {exc}", label, line)
                )
                continue
            if len(documents) != 1 or not isinstance(documents[0], dict):
                issues.append(
                    CapabilityIssue(
                        "AUXILIARY_PARSE",
                        "expected exactly one YAML mapping",
                        label,
                    )
                )

    def _build_provenance(
        self,
        root: Path,
        files: list[Path],
        exact_roles: dict[Path, SourceRole],
        directory_roles: dict[Path, SourceRole],
        manifest: CapabilityManifest | None,
        issues: list[CapabilityIssue],
    ) -> tuple[SourceProvenance, ...]:
        manifest_path = root / "zana.yaml"
        all_paths = ([manifest_path] if manifest_path.is_file() else []) + files
        provenance: list[SourceProvenance] = []
        for path in sorted(set(all_paths)):
            try:
                digest, size = sha256_of(path)
            except OSError as exc:
                issues.append(
                    CapabilityIssue(
                        "PROVENANCE_READ",
                        f"cannot hash source file: {exc}",
                        relative_label(root, path),
                    )
                )
                continue
            role = self._role_for_file(path, exact_roles, directory_roles, root)
            label = relative_label(root, path)
            if role is SourceRole.MANIFEST and manifest is not None:
                title = manifest.name
                title_origin = "manifest"
            else:
                title = path.stem
                title_origin = "file_stem"
            usage = self._declared_usage(manifest)
            provenance.append(
                make_provenance(
                    relative_path=label,
                    sha256=digest,
                    size_bytes=size,
                    role=role,
                    title=title,
                    title_origin=title_origin,
                    declared_license=manifest.license if manifest is not None else None,
                    usage_metadata=usage,
                    ingested_at=self._ingested_at,
                )
            )
        return tuple(sorted(provenance, key=lambda item: item.relative_path))

    @staticmethod
    def _role_for_file(
        path: Path,
        exact_roles: dict[Path, SourceRole],
        directory_roles: dict[Path, SourceRole],
        root: Path,
    ) -> SourceRole:
        if path in exact_roles:
            return exact_roles[path]
        resolved = path.resolve()
        for directory, role in directory_roles.items():
            if resolved != directory and directory in resolved.parents:
                return role
        try:
            rel = path.relative_to(root.resolve()).as_posix()
        except ValueError:
            return SourceRole.OTHER
        if rel == "zana.yaml":
            return SourceRole.MANIFEST
        parts = rel.split("/")
        if len(parts) > 1:
            head = parts[0]
            if head == "behavior":
                return SourceRole.BEHAVIOR
            if head == "knowledge":
                return SourceRole.KNOWLEDGE
            if head == "training":
                return SourceRole.TRAINING
            if head in ("evals", "evaluation"):
                return SourceRole.EVALUATION
            if head == "tools":
                return SourceRole.TOOLS
            if head == "permissions":
                return SourceRole.PERMISSIONS
        return SourceRole.OTHER

    @staticmethod
    def _declared_usage(manifest: CapabilityManifest | None) -> dict[str, object]:
        if manifest is None:
            return {}
        usage: dict[str, object] = {}
        if manifest.description is not None:
            usage["description"] = manifest.description
        if manifest.goal is not None:
            usage["goal_type"] = manifest.goal.type
        if manifest.knowledge is not None and manifest.knowledge.citationRequired is not None:
            usage["citation_required"] = manifest.knowledge.citationRequired
        if manifest.training is not None and manifest.training.goal is not None:
            usage["training_goal"] = manifest.training.goal
        return usage
