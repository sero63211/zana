# ZANA Decision Records

Decision records capture durable coordination decisions that affect multiple
agents. They never override `docs/goals/zana-mvp/state.yaml`.

## Format

Each record:

- id and date
- status (proposed, accepted, superseded)
- context and decision
- affected contracts and owners
- what it supersedes

Keep records bounded and factual. When a decision changes, add a new record
that explicitly supersedes the old one.
