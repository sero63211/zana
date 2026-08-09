"""Bounded capability probe tests with honest unknown values."""

from __future__ import annotations

import stat

from tests.platform.helpers import mac_layout, resolver_for
from zana_core.platform.models import FilesystemCapability
from zana_core.platform.probe import DefaultFilesystemProbe, probe_roots


def test_probe_existing_root(tmp_path):
    paths = resolver_for(mac_layout(tmp_path)).resolve()
    from zana_core.platform.ensure import ensure_roots

    ensure_roots(paths)
    probe = DefaultFilesystemProbe()
    capability = probe.probe(paths.data_root)
    assert capability.available is True
    assert capability.writable is True
    assert capability.free_bytes is not None and capability.free_bytes > 0
    assert capability.error is None


def test_probe_missing_root_is_unknown_not_fake_zero(tmp_path):
    missing = tmp_path / "missing"
    capability = DefaultFilesystemProbe().probe(missing)
    assert capability.available is False
    assert capability.free_bytes is None
    assert capability.error is not None


def test_probe_readonly_root_writable_false(tmp_path):
    root = tmp_path / "ro"
    root.mkdir()
    root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        capability = DefaultFilesystemProbe().probe(root)
        assert capability.available is True
        assert capability.writable is False
    finally:
        root.chmod(stat.S_IRWXU)


def test_probe_roots_constant_shape(tmp_path):
    paths = resolver_for(mac_layout(tmp_path)).resolve()
    capabilities = probe_roots(paths)
    assert len(capabilities) == 6
    assert all(isinstance(item, FilesystemCapability) for item in capabilities)


def test_injected_probe_is_used(tmp_path):
    class FixedProbe:
        def probe(self, root):
            return FilesystemCapability(
                root=root,
                available=True,
                writable=True,
                free_bytes=123,
                error=None,
            )

    paths = resolver_for(mac_layout(tmp_path)).resolve()
    capabilities = probe_roots(paths, FixedProbe())
    assert all(item.free_bytes == 123 for item in capabilities)
