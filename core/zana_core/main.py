"""ZANA Core CLI and FastAPI app factory.

Entry point: zana-core serve --host 127.0.0.1 --port <n> [--token <token>]
Token may also be supplied via ZANA_CORE_TOKEN environment variable.
"""

from __future__ import annotations

import os
import sys

import click
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR

from zana_core import __version__
from zana_core.api.deps import ServerConfig

# Module-level reference to the current server config, set by the CLI
_config: ServerConfig | None = None


def _current_config() -> ServerConfig:
    """Return the current per-launch config. Called by the auth dependency."""
    assert _config is not None, "Server not configured; use zana-core serve"
    return _config


def create_app(token: str) -> FastAPI:
    """Create and configure a FastAPI application with the given launch token."""
    global _config
    _config = ServerConfig(token=token, version=__version__)

    app = FastAPI(
        title="ZANA Core",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Strict CORS: only loopback and Tauri origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1",
            "http://localhost",
            "tauri://localhost",
            "https://tauri.localhost",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Catch-all: no raw tracebacks in error responses
    @app.exception_handler(Exception)
    async def catchall_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An internal error occurred.",
                    "details": {},
                    "recoverable": False,
                    "actions": [],
                }
            },
        )

    # Pass-through Starlette HTTPException dict details as-is
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    # Canonical validation error shape
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed.",
                    "details": {"errors": exc.errors()},
                    "recoverable": True,
                    "actions": ["fix_request_payload"],
                }
            },
        )

    # Register routers
    from zana_core.api.health import router as health_router

    app.include_router(health_router)

    return app


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """ZANA Core backend sidecar."""
    if ctx.invoked_subcommand is None:
        click.echo("Usage: zana-core [COMMAND]")
        click.echo("Try 'zana-core serve --help'")
        ctx.exit(1)


@cli.command()
@click.option("--host", default="127.0.0.1", help="Bind address (loopback only)")
@click.option("--port", default=8000, type=int, help="Listen port")
@click.option(
    "--token",
    default=None,
    help="Per-launch bearer token. Falls back to ZANA_CORE_TOKEN env var.",
)
def serve(host: str, port: int, token: str | None) -> None:
    """Start the ZANA Core API server."""
    resolved = token or os.environ.get("ZANA_CORE_TOKEN")

    if not resolved:
        click.echo(
            "Error: A non-empty launch token is required.\n"
            "Provide it via --token or the ZANA_CORE_TOKEN environment variable.",
            err=True,
        )
        sys.exit(1)

    if host not in ("127.0.0.1", "localhost", "::1"):
        click.echo(
            "Error: host must be a loopback address (127.0.0.1, localhost, or ::1).",
            err=True,
        )
        sys.exit(1)

    app = create_app(token=resolved)
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=True)


if __name__ == "__main__":
    cli()
