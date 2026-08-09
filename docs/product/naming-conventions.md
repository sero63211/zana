# ZANA Naming Conventions

Use the terms below consistently in code, API payloads, UI copy, documentation,
tests, and artifact metadata. Product language is a contract, not decoration.

## Canonical product names

| Canonical term | Meaning | Avoid |
| --- | --- | --- |
| Runtime | Local or self-hosted inference program. | Calling a Runtime a model |
| Base Model | Exact model identity exposed by a Runtime/training backend. | Generic `model` where ambiguity matters |
| Capability Source | Editable specialization source material. | Capability package when referring to source |
| Build Plan | Reviewable specialization decision. | Build config when it means the plan |
| Build Job | One persisted plan execution. | Build run for durable identifiers |
| Candidate | Temporary result awaiting verification. | Image before evaluation passes |
| ZANA Image | Immutable content-addressed output. | Mutable deployment/state |
| ZANA Instance | Mutable running state created from an Image. | Image when referring to chat or memory |
| Knowledge Snapshot | Immutable ingested knowledge and index identity. | Folder/document set without digest context |
| Verification Report | Immutable baseline/candidate evaluation evidence. | Scorecard when provenance matters |

Use `ZANA Image` and `ZANA Instance` with capitalization in user-facing prose;
use `zana_image` and `zana_instance` for identifiers where the surrounding
language requires snake case.

## Modules, files, routes, and identifiers

- Python modules, functions, variables, schema fields, and API JSON fields use
  `snake_case`; classes and Pydantic models use `PascalCase`.
- TypeScript components, component files, and exported types use `PascalCase`;
  hooks begin with `use` (for example, `useCoreHealth.ts`).
- Non-component TypeScript modules use concise `camelCase` filenames only where
  established in their directory; preserve existing local convention when
  evolving a file. URL routes and hash route ids use lowercase kebab-case.
- API paths use lowercase plural nouns, such as `/api/v1/build-jobs`; request
  and response fields remain `snake_case` to match Core contracts.
- Database tables and durable fields use lowercase `snake_case`. Stable IDs use
  explicit entity prefixes where useful (for example, `job_id`, `image_id`).
- Job states and persisted strategy values are uppercase enum tokens, such as
  `PLANNED`, `RAG_ONLY`, and `LORA_TOOLS_RAG`. UI labels convert them to
  readable words without changing their stored value.
- Artifact names use a human-readable name plus SemVer when applicable;
  immutable identity uses a digest. Do not treat a version label as an integrity
  identifier.

## Evolve in place

Update an existing file when it owns the behavior or documentation being
changed. Do not create replacement files that encode revision history in the
name. This applies to source, tests, docs, manifests, and generated-artifact
inputs.

The following filename patterns are prohibited:

```text
*_v2
*_v4
*_final
*_new
*-YYYY-MM-DD
*_YYYYMMDD
```

The ban includes equivalent date-suffixed replacement filenames. Versions
belong in manifests, artifact metadata, database migrations, or Git history;
not in ad hoc replacement filenames. A migration name may use its ordered
migration identifier where the migration framework requires it, but it must not
be a duplicate copy of a current source file.

## Naming checks before adding a file

1. Search for the current owner with `rg` and extend it if appropriate.
2. Choose a name that states the enduring responsibility, not a temporary
   implementation phase.
3. Keep product terms aligned with the canonical glossary above.
4. Put revisions in Git, migrations, manifests, or artifact metadata instead
   of a filename suffix.

