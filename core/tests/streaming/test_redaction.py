"""Bounded recursive secret redaction tests."""

from __future__ import annotations

from zana_core.streaming.redaction import (
    REDACTED,
    RedactionLimits,
    Redactor,
    is_sensitive_key,
    redact_value,
)


class TestRecursiveRedaction:
    def test_secret_values_removed_recursively(self) -> None:
        payload = {
            "safe": "keep me",
            "authorization": "Bearer secret",
            "nested": {
                "password": "p",
                "cookie": "c",
                "api_key": "k",
                "ok": "value",
            },
            "list": [
                {"token": "t", "safe": 1},
                "plain",
            ],
        }
        result = redact_value(payload)
        assert result["safe"] == "keep me"
        assert result["authorization"] == REDACTED
        assert result["nested"]["password"] == REDACTED
        assert result["nested"]["cookie"] == REDACTED
        assert result["nested"]["api_key"] == REDACTED
        assert result["nested"]["ok"] == "value"
        assert result["list"][0]["token"] == REDACTED
        assert result["list"][0]["safe"] == 1

    def test_sensitive_key_normalization(self) -> None:
        assert is_sensitive_key("Authorization")
        assert is_sensitive_key("x-api-key")
        assert is_sensitive_key("access_token")
        assert not is_sensitive_key("result")
        assert not is_sensitive_key("token_count")

    def test_depth_bound(self) -> None:
        value = "x"
        for _ in range(30):
            value = {"next": value}
        result = redact_value(value, RedactionLimits(max_depth=3))
        current = result
        for _ in range(3):
            current = current["next"]
        assert current == REDACTED

    def test_items_bound(self) -> None:
        payload = {f"k{i}": "v" for i in range(5)}
        result = redact_value(payload, RedactionLimits(max_items=2))
        redacted = [item for item in result.values() if item == REDACTED]
        assert len(redacted) >= 3

    def test_safe_string_truncated(self) -> None:
        result = redact_value("x" * 100, RedactionLimits(max_string_length=20))
        assert len(result) <= 20
        assert result.endswith("[truncated]")

    def test_redactor_protocol_use(self) -> None:
        redactor = Redactor(RedactionLimits(max_string_length=10))
        assert redactor.redact({"secret": "s"})["secret"] == REDACTED
