"""Hidden code, install hooks, and auxiliary YAML rejection tests."""

from __future__ import annotations

import pytest

from tests.capabilities.helpers import (
    build_math_example,
    make_validator,
    write,
)
from zana_core.capabilities.errors import CapabilitySourceValidationError


def codes(exc: CapabilitySourceValidationError) -> list[str]:
    return [issue.code for issue in exc.issues]


def expect_hook_failure(root) -> None:
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "HOOK_PROHIBITED" in codes(exc_info.value), str(exc_info.value)


def test_shell_script_rejected(tmp_path):
    root = build_math_example(tmp_path)
    write(root, "knowledge/sources/install.sh", "#!/bin/sh\necho pwned\n")
    expect_hook_failure(root)


def test_python_file_rejected(tmp_path):
    root = build_math_example(tmp_path)
    write(root, "knowledge/sources/helper.py", "import os\n")
    expect_hook_failure(root)


def test_hooks_directory_rejected(tmp_path):
    root = build_math_example(tmp_path)
    write(root, "hooks/postinstall.sh", "echo hi\n")
    expect_hook_failure(root)


def test_executable_bit_rejected(tmp_path):
    root = build_math_example(tmp_path)
    target = write(root, "knowledge/sources/tool.dat", "plain data\n")
    target.chmod(0o755)
    expect_hook_failure(root)


def test_shebang_rejected(tmp_path):
    root = build_math_example(tmp_path)
    write(root, "knowledge/sources/runner", "#!/bin/sh\necho hi\n")
    expect_hook_failure(root)


def test_auxiliary_yaml_parse_error_rejected(tmp_path):
    root = build_math_example(tmp_path)
    write(root, "tools/tools.yaml", "tools: [unclosed\n")
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "AUXILIARY_PARSE" in codes(exc_info.value)
    assert any(issue.file == "tools/tools.yaml" for issue in exc_info.value.issues)


def test_auxiliary_yaml_duplicate_key_rejected(tmp_path):
    root = build_math_example(tmp_path)
    write(root, "permissions/policy.yaml", "network:\n  outbound: false\n  outbound: true\n")
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "AUXILIARY_DUPLICATE_KEY" in codes(exc_info.value)
    assert any(issue.line == 3 for issue in exc_info.value.issues)


def test_non_code_auxiliary_yaml_ok(tmp_path):
    root = build_math_example(tmp_path)
    result = make_validator().validate(root)
    assert result is not None
