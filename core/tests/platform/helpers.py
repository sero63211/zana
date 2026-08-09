"""Shared locators and fixtures for platform path tests."""

from __future__ import annotations

from pathlib import Path

from zana_core.platform.models import PathRoot
from zana_core.platform.resolve import FixedPathLocator, PathResolver


def mac_layout(base: Path) -> dict[PathRoot, Path]:
    return {
        PathRoot.CONFIG: base / "Library" / "Preferences" / "zana",
        PathRoot.DATA: base / "Library" / "Application Support" / "zana",
        PathRoot.CACHE: base / "Library" / "Caches" / "zana",
        PathRoot.LOG: base / "Library" / "Logs" / "zana",
        PathRoot.TEMP: base / "tmp" / "zana",
        PathRoot.WORKSPACE: base / "Library" / "Application Support" / "zana" / "workspaces",
    }


def linux_layout(base: Path) -> dict[PathRoot, Path]:
    return {
        PathRoot.CONFIG: base / ".config" / "zana",
        PathRoot.DATA: base / ".local" / "share" / "zana",
        PathRoot.CACHE: base / ".cache" / "zana",
        PathRoot.LOG: base / ".local" / "state" / "zana" / "log",
        PathRoot.TEMP: base / "tmp" / "zana",
        PathRoot.WORKSPACE: base / ".local" / "share" / "zana" / "workspaces",
    }


def windows_layout(base: Path) -> dict[PathRoot, Path]:
    return {
        PathRoot.CONFIG: base / "AppData" / "Roaming" / "zana",
        PathRoot.DATA: base / "AppData" / "Local" / "zana",
        PathRoot.CACHE: base / "AppData" / "Local" / "zana" / "Cache",
        PathRoot.LOG: base / "AppData" / "Local" / "zana" / "Logs",
        PathRoot.TEMP: base / "Temp" / "zana",
        PathRoot.WORKSPACE: base / "AppData" / "Local" / "zana" / "Workspaces",
    }


def resolver_for(layout: dict[PathRoot, Path], **overrides) -> PathResolver:
    return PathResolver(
        locator=FixedPathLocator(layout),
        overrides=overrides,
    )
