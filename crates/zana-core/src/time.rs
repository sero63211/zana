//! Deterministic UTC timestamps matching the accepted Python ISO format.

use std::time::{SystemTime, UNIX_EPOCH};

/// Return `YYYY-MM-DDTHH:MM:SS.microseconds+00:00` in UTC, the exact shape
/// SQLAlchemy/`datetime.now(UTC).isoformat()` writes to SQLite.
pub fn now_iso() -> String {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let seconds = duration.as_secs();
    let micros = duration.subsec_micros();
    let days = seconds / 86_400;
    let day_seconds = seconds % 86_400;
    let (year, month, day) = civil_from_days(days as i64);
    let hour = day_seconds / 3600;
    let minute = (day_seconds % 3600) / 60;
    let second = day_seconds % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{micros:06}+00:00")
}

/// Howard Hinnant's days-to-civil algorithm.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = (if mp < 10 { mp + 3 } else { mp - 9 }) as u32;
    (if month <= 2 { year + 1 } else { year }, month, day)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_shape_matches_python() {
        let value = now_iso();
        assert!(value.ends_with("+00:00"));
        assert_eq!(value.len(), 32);
        assert_eq!(&value[10..11], "T");
        assert_eq!(&value[19..20], ".");
    }

    #[test]
    fn epoch_is_known_civil_date() {
        let (year, month, day) = civil_from_days(0);
        assert_eq!((year, month, day), (1970, 1, 1));
        let (year, month, day) = civil_from_days(19_723);
        assert_eq!((year, month, day), (2024, 1, 1));
    }
}
