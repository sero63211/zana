# T922 Evaluation and Benchmark Policy

## Product decision

Every capability build must support a paired baseline-versus-candidate
evaluation. The baseline and candidate use the same exact model identity,
runtime version, prompt template, generation settings, benchmark revision,
hardware class and resource budget. The only permitted difference is the
intended capability delta: approved knowledge/retrieval, adapter, prompt/tool
package, or other declared build output.

The result is not a single marketing score. ZANA records per-task outcomes,
aggregate score, failures/regressions, latency, peak memory, throughput, output
size, configuration hash and provenance. The UI presents `Before`, `After`,
absolute/relative `Delta`, the gate verdict and every regressed category.

## Reproducibility and contamination rules

- Pin the benchmark source revision and exact imported artifact digest.
- Record license/terms acceptance; registry metadata never authorizes a data
  download or redistribution by itself.
- Keep train/development/knowledge inputs disjoint from the held-out benchmark
  by content digest and source identity. A collision blocks the comparison.
- Run deterministic exact/normalized scoring where possible. Stochastic or
  judge-based scoring must be explicitly labeled, repeatable and optional.
- Compare paired items and retain bounded evidence. Missing, cancelled,
  unsupported or partially scored runs never become a green gate.
- Domain improvement cannot hide general regressions. Each build may declare a
  target suite and a separate regression suite with explicit thresholds.
- Medical scores are benchmark evidence, never a safety certification,
  diagnosis claim or permission for autonomous clinical action.

## Source-backed initial registry candidates

Sources inspected on 2026-08-11. ZANA may ship only pinned registry metadata;
dataset bytes require a separate license, digest, disk and consent gate.

- [MATH](https://github.com/hendrycks/math/tree/985bdc1696e88e8643f081a0ff4719da39f2ae2a),
  author repository, MIT metadata, for deterministic mathematical reasoning.
- [MMLU](https://github.com/hendrycks/test/tree/4450500f923c49f1fb1dd3d99108a0bd9717b660),
  author repository, MIT metadata, including medical and mathematical subsets;
  subset identity and prompt protocol must be recorded.
- [PubMedQA](https://github.com/pubmedqa/pubmedqa/tree/1cbae8e92f72f20c8d3747cbb3bf5bc53554d997),
  author repository, MIT metadata, for biomedical research question answering.
- [MedQA](https://github.com/jind11/MedQA/tree/27b02f66aac217933c9648a06f82e9f720377925),
  author repository with MIT code metadata, but the README points to separately
  hosted exam/textbook data; those bytes stay gated pending their own terms and
  redistribution review.
- [GSM8K](https://github.com/openai/grade-school-math/tree/3101c7d5072418e28b9008a6636bde82a006892c),
  official archived OpenAI repository. GitHub exposes no repository license at
  the inspected revision, so ZANA must not bundle or auto-download its data
  without an explicit legal/license decision.
- [OpenAI simple-evals](https://github.com/openai/simple-evals/tree/652c89d0ca9df547706735883097e9537d40dc47)
  is an MIT reference for evaluation protocols only; the final ZANA product
  implements its evaluation authority in Rust and does not ship a Python eval
  runtime.
- [AndroidWorld](https://github.com/google-research/android_world/tree/3e50888527ef9f29b9157ecd537e408008bb1c85),
  Google Research, Apache-2.0 metadata, is a later Android-agent evaluation
  candidate. It is not a reason to make Android the primary ZANA product flow
  and no emulator/device run is authorized in the current host-safe tranche.

## Custom knowledge benchmarks

For Kurdish tutoring, private documents and specialist domains, users can
import a small held-out benchmark package with question, accepted answer or
rubric, language/skill tags and provenance. ZANA must prove those exact items
and near-duplicate content were not included in training or retrieval sources.
The same mechanism supports community benchmark packages without hard-coding
one language, profession, device or deployment target into ZANA Images.

## Required Rust lifecycle

`Benchmark Package -> Baseline Run -> Capability Build -> Candidate Run -> Paired Comparison -> Gate -> Image eligibility`

An Image may be created only when required target thresholds pass and declared
regression limits are respected. Results and manifests are immutable evidence;
logs remain bounded and redacted, while cancellation/retry state belongs to the
job system and mutable Instance/runtime state remains outside the Image.
