# T920 Founder Directive: Rust Core and Android Agent

## Decision

The shipped authoritative ZANA Core is now a Rust target. Existing accepted
Python code and tests are migration evidence, not the final product runtime.
Python may remain only behind an optional isolated provider boundary where a
required third-party tool has no production Rust implementation.

Android is a first-class deployment/runtime target. It must not become a
special-purpose replacement for ZANA Images. A medical assistant, Kurdish
language tutor, private-document specialist, Android device agent, or another
capability all use the same lifecycle:

`Capability Source -> Build Plan -> Evaluation -> immutable ZANA Image -> platform-specific mutable Instance`

The Android application is an installed runtime. A ZANA Image is imported into
that runtime and references an exact compatible base model by digest. Device
permissions, secrets, conversations, granted Android actions, and mutable memory
belong to the Android Instance, never to the immutable Image.

## Target architecture

- shared Rust Core: contracts, errors, identity/digests, permission decisions,
  resource admission, jobs, persistence, capability/build/evaluation/Image/
  Instance/portability services, and platform-neutral runtime interfaces;
- Rust desktop host/server: loopback-authenticated compatibility API during
  migration plus direct Tauri integration where it reduces process overhead;
- native Android application: Kotlin/Jetpack Compose and Android lifecycle,
  AppFunctions, Intents, Storage Access Framework, speech/foreground-service
  surfaces, and explicit OS permission acquisition;
- Android Rust bridge: narrow generated/JNI-compatible bindings to the shared
  Core; no duplicated security or Image-validation logic in Kotlin;
- local mobile inference: LiteRT-LM Kotlin/native runtime with manual tool-call
  return to the Rust permission engine; automatic model-side tool execution is
  forbidden;
- optional OEM/managed target: Device Owner or AOSP system service, never a
  custom Linux kernel unless an actual hardware-driver requirement is proven.

## FunctionGemma preflight

Official metadata inspected on 2026-08-11:

- repository: `google/functiongemma-270m-it`;
- access: manual gated Gemma license; unauthenticated HEAD returns 401;
- mobile artifact: `tiny_garden.litertlm`;
- exact size: `288440320` bytes;
- exact SHA-256: `c0b243c22553d4cb8451eade37710199b33b9002c58752efc0094e0fed5ad1c2`;
- host preflight: Apple M2 Pro / 16 GiB RAM; 62% system memory free; no
  swap-ins/outs observed; 32 GiB disk free.

No model byte may be downloaded until the Founder accepts the upstream Gemma
license and provides authenticated repository access. The first allowed smoke
is one bounded artifact download, digest verification, LiteRT-LM format probe,
single-model load, one deterministic manual function-call prompt, unload, and
artifact/cache accounting. No training or additional model is authorized by
this directive.

## Migration invariants

- preserve accepted error, redaction, path, digest, transaction, cancellation,
  job/event, permission, and resource semantics;
- never wire a foundation-only Rust server over the functional desktop Core;
  switch packaging atomically only after Rust route parity is proven;
- no big-bang deletion of Python before Rust parity and rollback evidence;
- no new Python product features;
- no UI-owned authority or duplicated Android permission truth;
- no automatic model download, silent model substitution, or unconfirmed tool
  action;
- unchanged canonical filenames; internal schema versions may evolve without
  filename suffixes such as `v2` or `v4`;
- focused commits, clean worktrees, non-force integration, and durable receipts.
