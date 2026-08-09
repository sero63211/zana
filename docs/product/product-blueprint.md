# ZANA Product Blueprint

## Purpose and boundary

ZANA builds verified, portable AI instances from local or self-hosted models
already controlled by the operator. It owns the lifecycle between a Capability
Source and a reproducible ZANA Image, then separates that immutable Image from
the mutable Instance used at runtime.

ZANA does not build foundation models, host a public marketplace, provide cloud
sync or billing, replace inference runtimes, replace MCP or OCI, or make model
licensing decisions. It reuses stable standards where they fit: OCI image
layouts, SHA-256 digests, SemVer, JSON Schema, and OpenAI-compatible local APIs.

## Durable lifecycle

```text
Capability Source
  -> runtime and hardware analysis
  -> baseline evaluation
  -> reviewable Build Plan
  -> Build Job and candidate artifacts
  -> post-build evaluation
  -> verified immutable ZANA Image
  -> mutable ZANA Instance
  -> inspected local use, export/import, or rollback
```

A failed candidate remains historical evidence and must not replace the most
recent verified Image. A Verification Report is bound to the exact model,
image, evaluation suite, and runtime/configuration metadata used to create it.

## Core entities and boundaries

| Entity | Responsibility | Mutability |
| --- | --- | --- |
| Runtime | A local/self-hosted program exposing models for inference. | Observed state |
| Base Model | Exact identity visible through a Runtime or training backend. | Referenced identity |
| Capability Source | Editable behavior, knowledge, examples, tools, permissions, and evaluations. | Mutable source |
| Build Plan | User-reviewable strategy and reasons. | Frozen for its Build Job |
| Build Job | One persisted execution against exact inputs. | Historical after completion |
| Knowledge Snapshot | Hashed documents, normalized content, chunking, embedding identity, and index/recipe. | Immutable |
| Adapter | Compatible parameter-efficient artifact, if training is valid. | Immutable in an Image |
| ZANA Image | Content-addressed output and provenance; may reference base weights. | Immutable |
| ZANA Instance | Conversations, approved memory, runtime caches, settings, and secret references. | Mutable |

## User flow

The MVP flow begins without an account or mandatory download. The user scans
for a supported Runtime, selects a real Base Model, creates a Capability Source,
and adds local knowledge and evaluation cases. ZANA profiles compatibility and
resources, proposes an allowed Build Plan, and discloses any download, disk,
or permission consequences before work starts.

Build, ingestion, indexing, training, evaluation, packing, import, and export
are persisted jobs with phase-aware progress, cancellation, recoverable errors,
and durable event history. A passing result creates a verified Image. Starting
an Instance from it exposes mutable state and answer provenance without
rewriting the Image.

## Product integrity rules

- Do not invent records, detected models, citations, metrics, or success.
- A Runtime is never conflated with a Base Model.
- Training is optional; the planner may use non-training strategies when the
  hardware or exact model identity is incompatible.
- Images are immutable and content-addressed; Instances carry mutable state.
- Evaluation gates determine verification. A green UI state requires real
  evidence, and a failed gate remains visibly failed.
- Imported artifacts are validated before registration. A missing Base Model is
  reported, never silently acquired or inferred.

## Local-first safety rules

All data remains local unless the operator explicitly authorizes an external
feature. Network policy is offline by default after acquisition; model or
artifact downloads require confirmation. Tools, filesystem mounts, and secrets
are deny-by-default and must have explicit scopes. Documents are untrusted
evidence, not executable instructions. Capability imports cannot run arbitrary
scripts, hooks, shell commands, or embedded Python.

## Present delivery state

The product currently provides an authenticated Core/Tauri foundation and a
seven-view React shell. Live behavior is limited to Core health. Discovery,
editing, jobs, model use, evaluation, Images, Instances, and transport are
planned product capabilities and must not be represented as working until their
real local implementations and acceptance evidence exist.

