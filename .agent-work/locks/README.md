# ZANA Lock Convention

Locks prevent overlapping write ownership on shared paths during parallel work.
They are transient coordination state, never authoritative board truth.

## Rules

- One lock per owner scope: `<task-id>-<scope>.lock`.
- Contents: owner agent, held paths, held-until handoff id, created timestamp.
- The owner releases the lock by deleting it after its handoff exists.
- Do not edit or steal a held lock; escalate to the PM/lead instead.
- Lock files are ignored by Git through `*.tmp` patterns when transient.

No lock is currently held. T003 completed with no concurrent conflicts.
