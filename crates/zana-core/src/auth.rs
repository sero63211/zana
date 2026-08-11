//! Per-launch bearer token verification.

/// Hard bound for token comparisons. Valid launch tokens are short; anything
/// longer is rejected before comparison work.
pub const MAX_TOKEN_BYTES: usize = 512;

pub fn extract_bearer_token(authorization: Option<&str>) -> Option<&str> {
    let authorization = authorization?;
    let mut parts = authorization.split_whitespace();
    let scheme = parts.next()?;
    let token = parts.next()?;
    if parts.next().is_some() || !scheme.eq_ignore_ascii_case("bearer") || token.is_empty() {
        return None;
    }
    Some(token)
}

/// Validate a launch token before it reaches verification or startup.
///
/// The same predicate guards startup and every authenticated request so the
/// two paths cannot drift: empty, oversized, or whitespace-containing tokens
/// are rejected before any filesystem work or comparison.
pub fn valid_launch_token(token: &str) -> bool {
    !token.is_empty() && token.len() <= MAX_TOKEN_BYTES && !token.chars().any(char::is_whitespace)
}

/// Compare two tokens without an early length exit.
///
/// Both inputs are bounded to `MAX_TOKEN_BYTES`; the loop always walks the
/// full bound and folds presence (length) differences into the accumulator,
/// so a short token cannot be distinguished from a wrong-length token by an
/// early return. This is honest constant work for every accepted token shape.
pub fn constant_time_eq(left: &str, right: &str) -> bool {
    let left = left.as_bytes();
    let right = right.as_bytes();
    if left.len() > MAX_TOKEN_BYTES || right.len() > MAX_TOKEN_BYTES {
        return false;
    }
    let mut difference = 0u8;
    for index in 0..MAX_TOKEN_BYTES {
        let present_left = u8::from(index < left.len());
        let present_right = u8::from(index < right.len());
        difference |= present_left ^ present_right;
        let a = left.get(index).copied().unwrap_or(0);
        let b = right.get(index).copied().unwrap_or(0);
        difference |= a ^ b;
    }
    difference == 0
}

pub fn verify_token(expected: &str, authorization: Option<&str>) -> bool {
    if !valid_launch_token(expected) {
        return false;
    }
    match extract_bearer_token(authorization) {
        Some(provided) => valid_launch_token(provided) && constant_time_eq(expected, provided),
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_exact_bearer_token() {
        assert_eq!(extract_bearer_token(Some("Bearer abc")), Some("abc"));
        assert_eq!(extract_bearer_token(Some("bearer abc")), Some("abc"));
        assert_eq!(extract_bearer_token(Some("Bearer   abc")), Some("abc"));
        assert_eq!(extract_bearer_token(None), None);
        assert_eq!(extract_bearer_token(Some("Basic abc")), None);
        assert_eq!(extract_bearer_token(Some("Bearer")), None);
        assert_eq!(extract_bearer_token(Some("Bearer abc extra")), None);
    }

    #[test]
    fn constant_time_compare_rejects_wrong_tokens() {
        assert!(constant_time_eq("token-123", "token-123"));
        assert!(!constant_time_eq("token-123", "token-124"));
        assert!(!constant_time_eq("token-123", "token-1234"));
        assert!(!constant_time_eq("", "token"));
        assert!(!constant_time_eq("token", ""));
    }

    #[test]
    fn verify_token_requires_valid_bearer() {
        assert!(verify_token("secret", Some("Bearer secret")));
        assert!(!verify_token("secret", Some("Bearer wrong")));
        assert!(!verify_token("secret", None));
        assert!(!verify_token("secret", Some("secret")));
    }

    #[test]
    fn oversized_tokens_fail_closed_before_comparison() {
        let huge = "a".repeat(MAX_TOKEN_BYTES + 1);
        assert!(!constant_time_eq(&huge, &huge));
        assert!(!verify_token("secret", Some(&format!("Bearer {huge}"))));
    }

    #[test]
    fn valid_launch_token_rejects_empty_whitespace_and_oversized() {
        assert!(valid_launch_token("secret-token"));
        assert!(!valid_launch_token(""));
        assert!(!valid_launch_token("has space"));
        assert!(!valid_launch_token(" leading"));
        assert!(!valid_launch_token("trailing "));
        assert!(!valid_launch_token("tab\tseparated"));
        let huge = "a".repeat(MAX_TOKEN_BYTES + 1);
        assert!(!valid_launch_token(&huge));
    }

    #[test]
    fn verify_token_rejects_invalid_expected_token() {
        assert!(!verify_token("", Some("Bearer ")));
        assert!(!verify_token("has space", Some("Bearer has space")));
        assert!(!verify_token(
            &"a".repeat(MAX_TOKEN_BYTES + 1),
            Some(&format!("Bearer {}", "a".repeat(MAX_TOKEN_BYTES + 1)))
        ));
    }
}
