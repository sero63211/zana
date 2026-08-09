"""Decision engine: default deny, explicit allow, and escape rejection."""

from __future__ import annotations

from pathlib import Path

from zana_core.permissions.decisions import Decision, PermissionDecisionEngine
from zana_core.permissions.loader import load_policy


def _engine(yaml_text: str) -> PermissionDecisionEngine:
    return PermissionDecisionEngine(load_policy(yaml_text))


class TestDefaultDenyDecisions:
    def test_everything_is_denied_by_default(self) -> None:
        engine = _engine("schemaVersion: 1\n")
        assert engine.network_allowed() == Decision.DENY
        assert engine.tool_allowed("zana.calculator") == Decision.DENY
        assert engine.secret_allowed("api_key") == Decision.DENY
        assert engine.filesystem_allowed("filesystem_read", "/tmp/anything") == Decision.DENY
        assert engine.filesystem_allowed("filesystem_write", "/tmp/anything") == Decision.DENY
        assert engine.mcp_endpoint_allowed("mcp-a", "read") == Decision.DENY

    def test_offline_mode_denies_even_with_outbound_flag(self) -> None:
        engine = _engine("schemaVersion: 1\nnetwork:\n  mode: offline\n  outbound: true\n")
        assert engine.network_allowed() == Decision.DENY
        assert engine.network_denial().code == "NETWORK_DENIED"


class TestExplicitAllowDecisions:
    def test_network_ask_returns_ask(self) -> None:
        engine = _engine("schemaVersion: 1\nnetwork:\n  mode: ask\n  outbound: true\n")
        assert engine.network_allowed() == Decision.ASK
        assert engine.network_denial().code == "NETWORK_ASK_REQUIRED"

    def test_network_allowed_after_explicit_ask_approval(self) -> None:
        engine = _engine("schemaVersion: 1\nnetwork:\n  mode: ask\n  outbound: true\n")
        assert engine.network_allowed() == Decision.ASK
        assert engine.network_allowed(approved=True) == Decision.ALLOW

    def test_tool_and_secret_allowlist(self) -> None:
        engine = _engine(
            "schemaVersion: 1\ntools:\n  allow: [zana.calculator]\nsecrets:\n  allow: [api_key]\n"
        )
        assert engine.tool_allowed("zana.calculator") == Decision.ALLOW
        assert engine.tool_allowed("other") == Decision.DENY
        assert engine.secret_allowed("api_key") == Decision.ALLOW
        assert engine.secret_allowed("other_key") == Decision.DENY

    def test_filesystem_read_and_write_inside_mount_roots(self, tmp_path: Path) -> None:
        root = tmp_path / "mount"
        root.mkdir()
        engine = _engine(
            "schemaVersion: 1\n"
            "filesystem:\n"
            f"  read: [{root.as_posix()}]\n"
            f"  write: [{root.as_posix()}]\n"
        )
        inside = root / "data.txt"
        assert engine.filesystem_allowed("filesystem_read", inside) == Decision.ALLOW
        assert engine.filesystem_allowed("filesystem_write", inside) == Decision.ALLOW
        assert (
            engine.filesystem_allowed("filesystem_read", root / "sub" / "deep.txt")
            == Decision.ALLOW
        )

    def test_mcp_allowed_only_when_enabled_with_scope(self) -> None:
        engine = _engine(
            "schemaVersion: 1\n"
            "experimental:\n"
            "  mcp:\n"
            "    endpoints:\n"
            "      - id: mcp-a\n"
            "        endpoint: http://127.0.0.1:9000\n"
            "        scopes: [read]\n"
            "        enabled: true\n"
        )
        assert engine.mcp_endpoint_allowed("mcp-a", "read") == Decision.ALLOW
        assert engine.mcp_endpoint_allowed("mcp-a", "write") == Decision.DENY
        assert engine.mcp_endpoint_allowed("mcp-b", "read") == Decision.DENY


class TestEscapeRejection:
    def test_traversal_escape_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "mount"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("private", encoding="utf-8")
        engine = _engine(f"schemaVersion: 1\nfilesystem:\n  read: [{root.as_posix()}]\n")
        traversal = root / ".." / "outside.txt"
        assert engine.filesystem_allowed("filesystem_read", traversal) == Decision.DENY
        assert (
            engine.filesystem_denial("filesystem_read", traversal).code == "FILESYSTEM_READ_DENIED"
        )

    def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "mount"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("private", encoding="utf-8")
        link = root / "link.txt"
        link.symlink_to(outside)
        engine = _engine(f"schemaVersion: 1\nfilesystem:\n  read: [{root.as_posix()}]\n")
        assert engine.filesystem_allowed("filesystem_read", link) == Decision.DENY

    def test_write_outside_mount_root_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "mount"
        root.mkdir()
        engine = _engine(f"schemaVersion: 1\nfilesystem:\n  write: [{root.as_posix()}]\n")
        outside = tmp_path / "other.txt"
        assert engine.filesystem_allowed("filesystem_write", outside) == Decision.DENY
        assert (
            engine.filesystem_denial("filesystem_write", outside).code == "FILESYSTEM_WRITE_DENIED"
        )
