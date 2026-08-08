# ZANA

Local-first desktop application for building, evaluating, and running custom AI
capabilities. **M0 status**: authenticated health check between a Tauri 2
desktop shell and a FastAPI backend sidecar.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [pnpm](https://pnpm.io/) (Node package manager)
- [Rust](https://www.rust-lang.org/) (Tauri compilation)

No local models, Docker, or cloud accounts are required; the backend health
endpoint returns real process state without any external dependency.

## Repository layout

```
zana/
  core/              Python backend (FastAPI + Pydantic v2)
  apps/desktop/      Tauri 2 desktop shell (React + TypeScript)
  scripts/           Dev, test, and packaging helpers
  .github/           CI workflows
```

## Quick start

```bash
# Install Core dependencies
uv sync --project core

# Start the backend (dev token)
./scripts/dev.sh

# Or directly:
ZANA_CORE_TOKEN=dev-token uv run --project core zana-core serve
```

The server binds to `http://127.0.0.1:8000` and requires a bearer token on all
requests:

```bash
curl -H "Authorization: Bearer dev-token" http://127.0.0.1:8000/api/v1/health
```

## Testing

```bash
# Core backend tests, lint, and type check
./scripts/test.sh

# Individual steps:
uv run --project core pytest core/tests -v
uv run --project core ruff check core
uv run --project core pyright core/zana_core
```

## Interface contract

The desktop lane receives connection data via a Tauri invoke command and calls:

```
GET http://127.0.0.1:<port>/api/v1/health
Authorization: Bearer <token>
```

The token is generated per-launch by the desktop; the backend requires an
exact, non-empty token using constant-time comparison. Missing or wrong tokens
return `401` with a canonical error envelope.
