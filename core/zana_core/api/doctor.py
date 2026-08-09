"""Backward-compatible doctor router re-export.

The concrete ``/api/v1/system/doctor`` route now lives in
``zana_core.api.system`` and returns the bounded read-only
``DiagnosticReport``.  This module keeps the historical import path working so
consumers that imported ``router`` from ``zana_core.api.doctor`` do not break.
"""

from __future__ import annotations

from zana_core.api.system import router as router

__all__ = ["router"]
