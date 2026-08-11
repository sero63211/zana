//! Bounded HTTP/1.1 request and response primitives.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::{Duration, Instant};

pub const MAX_HEADER_BYTES: usize = 16 * 1024;
pub const MAX_BODY_BYTES: usize = 1024 * 1024;
pub const MAX_HEADER_COUNT: usize = 64;
pub const CONNECTION_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug)]
pub struct Request {
    pub method: String,
    pub path: String,
    pub headers: Vec<(String, String)>,
    pub body_len: usize,
}

impl Request {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    }

    pub fn origin(&self) -> Option<&str> {
        self.header("origin")
    }

    pub fn authorization(&self) -> Option<&str> {
        self.header("authorization")
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParseError {
    HeadersTooLarge,
    PayloadTooLarge,
    BadRequest,
    Timeout,
    Io,
}

#[derive(Debug)]
pub struct Response {
    pub status: u16,
    pub reason: &'static str,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

impl Response {
    pub fn json(status: u16, body: &[u8]) -> Self {
        Self {
            status,
            reason: reason_phrase(status),
            headers: vec![("content-type".to_owned(), "application/json".to_owned())],
            body: body.to_vec(),
        }
    }

    pub fn empty(status: u16) -> Self {
        Self {
            status,
            reason: reason_phrase(status),
            headers: Vec::new(),
            body: Vec::new(),
        }
    }

    pub fn with_header(mut self, name: &str, value: &str) -> Self {
        self.headers.push((name.to_owned(), value.to_owned()));
        self
    }

    pub fn with_headers(mut self, headers: Vec<(String, String)>) -> Self {
        self.headers.extend(headers);
        self
    }
}

pub fn reason_phrase(status: u16) -> &'static str {
    match status {
        200 => "OK",
        204 => "No Content",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        408 => "Request Timeout",
        413 => "Payload Too Large",
        431 => "Request Header Fields Too Large",
        500 => "Internal Server Error",
        503 => "Service Unavailable",
        _ => "Unknown",
    }
}

/// Reader boundary that can limit each blocking read to a caller-provided
/// remaining budget. The production socket implementation applies the timeout
/// on the underlying `TcpStream` before every read.
pub trait ReadWithTimeout {
    fn read_with_timeout(&mut self, buffer: &mut [u8], timeout: Duration)
        -> std::io::Result<usize>;
}

impl ReadWithTimeout for TcpStream {
    fn read_with_timeout(
        &mut self,
        buffer: &mut [u8],
        timeout: Duration,
    ) -> std::io::Result<usize> {
        self.set_read_timeout(Some(timeout))?;
        self.read(buffer)
    }
}

/// Read exactly one bounded request. Bodies are drained up to a hard cap so a
/// slow or oversized client cannot retain server state.
pub fn read_request<W: ReadWithTimeout>(reader: &mut W) -> Result<Request, ParseError> {
    read_request_with_clock(reader, &SystemClock, CONNECTION_TIMEOUT)
}

/// Monotonic clock boundary so request deadlines are testable without real
/// sleeps.
pub trait Clock {
    fn now(&self) -> Instant;
}

pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> Instant {
        Instant::now()
    }
}

/// Read one bounded request against one total monotonic deadline.
///
/// The deadline covers headers and body together. Rechecking it before every
/// read means successful partial reads and `Interrupted` retries can never
/// extend the budget. Each read is also limited to the remaining budget, so a
/// peer sending one byte just before the deadline cannot block for another
/// full idle timeout, and a read that crosses the deadline fails afterwards.
pub fn read_request_with_clock<W: ReadWithTimeout, C: Clock>(
    reader: &mut W,
    clock: &C,
    total_budget: Duration,
) -> Result<Request, ParseError> {
    let deadline = clock.now() + total_budget;
    let mut buffer = Vec::with_capacity(1024);
    let mut chunk = [0u8; 1024];
    let header_end;
    loop {
        if clock.now() >= deadline {
            return Err(ParseError::Timeout);
        }
        let remaining = deadline.saturating_duration_since(clock.now());
        if remaining.is_zero() {
            return Err(ParseError::Timeout);
        }
        match reader.read_with_timeout(&mut chunk, remaining) {
            Ok(0) => return Err(ParseError::BadRequest),
            Ok(count) => {
                if clock.now() >= deadline {
                    return Err(ParseError::Timeout);
                }
                buffer.extend_from_slice(&chunk[..count]);
                if let Some(position) = find_subsequence(&buffer, b"\r\n\r\n") {
                    header_end = position + 4;
                    break;
                }
                if buffer.len() > MAX_HEADER_BYTES {
                    return Err(ParseError::HeadersTooLarge);
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error)
                if error.kind() == std::io::ErrorKind::WouldBlock
                    || error.kind() == std::io::ErrorKind::TimedOut =>
            {
                return Err(ParseError::Timeout);
            }
            Err(_) => return Err(ParseError::Io),
        }
    }
    if header_end > MAX_HEADER_BYTES {
        return Err(ParseError::HeadersTooLarge);
    }

    let header_text =
        std::str::from_utf8(&buffer[..header_end]).map_err(|_| ParseError::BadRequest)?;
    let mut lines = header_text.split("\r\n");
    let request_line = lines.next().ok_or(ParseError::BadRequest)?;
    let mut parts = request_line.split(' ');
    let method = parts.next().ok_or(ParseError::BadRequest)?.to_owned();
    let target = parts.next().ok_or(ParseError::BadRequest)?;
    let version = parts.next().ok_or(ParseError::BadRequest)?;
    if parts.next().is_some()
        || !version.starts_with("HTTP/1.")
        || method.is_empty()
        || !method
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        || target.len() > MAX_HEADER_BYTES
        || !target.starts_with('/')
    {
        return Err(ParseError::BadRequest);
    }
    let path = target.split('?').next().unwrap_or(target);

    let mut headers = Vec::new();
    let mut content_length: Option<u64> = None;
    let mut transfer_encoding: Option<String> = None;
    for line in lines {
        if line.is_empty() {
            break;
        }
        if headers.len() >= MAX_HEADER_COUNT {
            return Err(ParseError::HeadersTooLarge);
        }
        let (name, value) = line.split_once(':').ok_or(ParseError::BadRequest)?;
        let name = name.trim().to_ascii_lowercase();
        let value = value.trim().to_owned();
        if name.is_empty() {
            return Err(ParseError::BadRequest);
        }
        if name == "content-length" {
            if content_length.is_some() {
                return Err(ParseError::BadRequest);
            }
            content_length = Some(value.parse().map_err(|_| ParseError::BadRequest)?);
        }
        if name == "transfer-encoding" {
            transfer_encoding = Some(value.clone());
        }
        headers.push((name, value));
    }

    if transfer_encoding.is_some_and(|value| !value.eq_ignore_ascii_case("identity")) {
        return Err(ParseError::BadRequest);
    }

    let content_length = content_length.unwrap_or(0);
    if content_length > MAX_BODY_BYTES as u64 {
        return Err(ParseError::PayloadTooLarge);
    }
    let body_len: usize = content_length
        .try_into()
        .map_err(|_| ParseError::BadRequest)?;

    // Bytes after the header terminator may already be buffered in the same
    // read. Consume them first so the parser never rereads them from the
    // socket; bytes beyond the declared body are a protocol violation.
    let mut remaining = body_len;
    let buffered = buffer.len().saturating_sub(header_end);
    if remaining == 0 {
        if buffered != 0 {
            return Err(ParseError::BadRequest);
        }
    } else {
        if buffered > remaining {
            return Err(ParseError::BadRequest);
        }
        remaining -= buffered;
    }
    let mut discard = [0u8; 4096];
    while remaining > 0 {
        if clock.now() >= deadline {
            return Err(ParseError::Timeout);
        }
        let remaining_budget = deadline.saturating_duration_since(clock.now());
        if remaining_budget.is_zero() {
            return Err(ParseError::Timeout);
        }
        let wanted = usize::min(discard.len(), remaining);
        match reader.read_with_timeout(&mut discard[..wanted], remaining_budget) {
            Ok(0) => return Err(ParseError::BadRequest),
            Ok(count) => {
                if clock.now() >= deadline {
                    return Err(ParseError::Timeout);
                }
                remaining -= count;
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error)
                if error.kind() == std::io::ErrorKind::WouldBlock
                    || error.kind() == std::io::ErrorKind::TimedOut =>
            {
                return Err(ParseError::Timeout);
            }
            Err(_) => return Err(ParseError::Io),
        }
    }

    Ok(Request {
        method,
        path: path.to_owned(),
        headers,
        body_len,
    })
}

pub fn write_response(stream: &mut TcpStream, response: &Response) -> std::io::Result<()> {
    let mut output = Vec::new();
    output.extend_from_slice(
        format!("HTTP/1.1 {} {}\r\n", response.status, response.reason).as_bytes(),
    );
    let mut has_content_length = false;
    for (name, value) in &response.headers {
        if name == "content-length" {
            has_content_length = true;
        }
        output.extend_from_slice(name.as_bytes());
        output.extend_from_slice(b": ");
        output.extend_from_slice(value.as_bytes());
        output.extend_from_slice(b"\r\n");
    }
    if !has_content_length {
        output.extend_from_slice(format!("content-length: {}\r\n", response.body.len()).as_bytes());
    }
    output.extend_from_slice(b"connection: close\r\n\r\n");
    output.extend_from_slice(&response.body);
    stream.write_all(&output)
}

fn find_subsequence(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() {
        return Some(0);
    }
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    impl ReadWithTimeout for &[u8] {
        fn read_with_timeout(
            &mut self,
            buffer: &mut [u8],
            _timeout: Duration,
        ) -> std::io::Result<usize> {
            Read::read(self, buffer)
        }
    }

    impl ReadWithTimeout for &mut &[u8] {
        fn read_with_timeout(
            &mut self,
            buffer: &mut [u8],
            _timeout: Duration,
        ) -> std::io::Result<usize> {
            Read::read(self, buffer)
        }
    }

    #[test]
    fn parses_valid_get_request() {
        let request = b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer token\r\n\r\n";
        let parsed = read_request(&mut &request[..]).expect("parses");
        assert_eq!(parsed.method, "GET");
        assert_eq!(parsed.path, "/api/v1/health");
        assert_eq!(parsed.header("authorization"), Some("Bearer token"));
    }

    #[test]
    fn rejects_oversized_headers() {
        let mut request = b"GET / HTTP/1.1\r\nX-Pad: ".to_vec();
        request.extend(std::iter::repeat_n(b'a', MAX_HEADER_BYTES + 1));
        request.extend_from_slice(b"\r\n\r\n");
        assert_eq!(
            read_request(&mut &request[..]).expect_err("rejects"),
            ParseError::HeadersTooLarge
        );
    }

    #[test]
    fn rejects_oversized_content_length() {
        let request = format!(
            "GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {}\r\n\r\n",
            MAX_BODY_BYTES + 1
        );
        assert_eq!(
            read_request(&mut request.as_bytes()).expect_err("rejects"),
            ParseError::PayloadTooLarge
        );
    }

    #[test]
    fn rejects_malformed_request_line() {
        let request = b"GET /api/v1/health\r\n\r\n";
        assert_eq!(
            read_request(&mut &request[..]).expect_err("rejects"),
            ParseError::BadRequest
        );
    }

    #[test]
    fn drains_bounded_body() {
        let request =
            b"POST /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 5\r\n\r\nhello";
        let parsed = read_request(&mut &request[..]).expect("parses");
        assert_eq!(parsed.body_len, 5);
        assert_eq!(parsed.method, "POST");
    }

    #[test]
    fn consumes_body_bytes_buffered_with_the_header() {
        let request =
            b"POST /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 5\r\n\r\nhello";
        let parsed = read_request(&mut SplitReader::new(request.to_vec(), 32)).expect("parses");
        assert_eq!(parsed.body_len, 5);
    }

    #[test]
    fn consumes_body_split_across_header_and_subsequent_reads() {
        let request =
            b"POST /api/v1/health HTTP/1.1\r\nContent-Length: 7\r\nX-Header: value\r\n\r\nbody-07";
        let parsed = read_request(&mut SplitReader::new(request.to_vec(), 16)).expect("parses");
        assert_eq!(parsed.body_len, 7);
    }

    #[test]
    fn rejects_headers_too_large_when_terminator_arrives_in_oversized_chunk() {
        let mut request = b"GET / HTTP/1.1\r\nX-Pad: ".to_vec();
        request.extend(std::iter::repeat_n(b'a', MAX_HEADER_BYTES + 1));
        request.extend_from_slice(b"\r\n\r\n");
        assert_eq!(
            read_request(&mut SplitReader::new(request, MAX_HEADER_BYTES + 8))
                .expect_err("rejects"),
            ParseError::HeadersTooLarge
        );
    }

    #[test]
    fn rejects_undisclosed_bytes_after_a_complete_request() {
        let request =
            b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\npipelined-next-request";
        assert_eq!(
            read_request(&mut &request[..]).expect_err("rejects"),
            ParseError::BadRequest
        );
    }

    #[test]
    fn repeated_partial_reads_cannot_extend_total_budget() {
        let start = Instant::now();
        let clock = FakeClock::new(start);
        let request = b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n";
        let mut reader =
            SlowReader::new(request.to_vec(), clock.clone(), Duration::from_millis(400));
        assert_eq!(
            read_request_with_clock(&mut reader, &clock, Duration::from_secs(1))
                .expect_err("times out"),
            ParseError::Timeout
        );
    }

    #[test]
    fn slow_body_drain_respects_total_deadline() {
        let start = Instant::now();
        let clock = FakeClock::new(start);
        let request = b"POST /api/v1/health HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello";
        let mut reader =
            SlowReader::new(request.to_vec(), clock.clone(), Duration::from_millis(300));
        assert_eq!(
            read_request_with_clock(&mut reader, &clock, Duration::from_secs(1))
                .expect_err("times out"),
            ParseError::Timeout
        );
    }

    #[test]
    fn interrupted_reads_cannot_evade_total_deadline() {
        let start = Instant::now();
        let clock = FakeClock::new(start);
        let mut reader = InterruptReader::new(clock.clone(), Duration::from_millis(700));
        assert_eq!(
            read_request_with_clock(&mut reader, &clock, Duration::from_secs(1))
                .expect_err("times out"),
            ParseError::Timeout
        );
    }

    #[test]
    fn single_read_crossing_budget_fails_after_the_read() {
        let start = Instant::now();
        let clock = FakeClock::new(start);
        let request = b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n";
        let mut reader =
            CrossingReader::new(request.to_vec(), clock.clone(), Duration::from_millis(1100));
        assert_eq!(
            read_request_with_clock(&mut reader, &clock, Duration::from_secs(1))
                .expect_err("crossing read must time out"),
            ParseError::Timeout
        );
    }

    #[test]
    fn remaining_read_timeout_decreases_across_reads() {
        let start = Instant::now();
        let clock = FakeClock::new(start);
        let request = b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n";
        let mut reader =
            TimeoutRecorder::new(request.to_vec(), clock.clone(), Duration::from_millis(300));
        assert_eq!(
            read_request_with_clock(&mut reader, &clock, Duration::from_secs(1))
                .expect_err("slow drip must time out"),
            ParseError::Timeout
        );
        let observed = reader.timeouts();
        assert!(
            observed.len() >= 3,
            "expected at least three bounded reads, got {observed:?}"
        );
        for pair in observed.windows(2) {
            assert!(
                pair[0] > pair[1],
                "remaining timeout must decrease: {observed:?}"
            );
        }
    }

    #[derive(Clone)]
    struct FakeClock {
        now: std::rc::Rc<std::cell::Cell<Instant>>,
    }

    impl FakeClock {
        fn new(start: Instant) -> Self {
            Self {
                now: std::rc::Rc::new(std::cell::Cell::new(start)),
            }
        }

        fn advance(&self, duration: Duration) {
            self.now.set(self.now.get() + duration);
        }
    }

    impl Clock for FakeClock {
        fn now(&self) -> Instant {
            self.now.get()
        }
    }

    struct SlowReader {
        data: Vec<u8>,
        offset: usize,
        clock: FakeClock,
        step: Duration,
    }

    impl SlowReader {
        fn new(data: Vec<u8>, clock: FakeClock, step: Duration) -> Self {
            Self {
                data,
                offset: 0,
                clock,
                step,
            }
        }
    }

    impl ReadWithTimeout for SlowReader {
        fn read_with_timeout(
            &mut self,
            output: &mut [u8],
            _timeout: Duration,
        ) -> std::io::Result<usize> {
            self.clock.advance(self.step);
            if self.offset >= self.data.len() || output.is_empty() {
                return Ok(0);
            }
            output[0] = self.data[self.offset];
            self.offset += 1;
            Ok(1)
        }
    }

    struct InterruptReader {
        clock: FakeClock,
        step: Duration,
    }

    impl InterruptReader {
        fn new(clock: FakeClock, step: Duration) -> Self {
            Self { clock, step }
        }
    }

    impl ReadWithTimeout for InterruptReader {
        fn read_with_timeout(
            &mut self,
            _output: &mut [u8],
            _timeout: Duration,
        ) -> std::io::Result<usize> {
            self.clock.advance(self.step);
            Err(std::io::Error::new(
                std::io::ErrorKind::Interrupted,
                "deterministic interruption",
            ))
        }
    }

    struct SplitReader {
        data: Vec<u8>,
        offset: usize,
        chunk: usize,
    }

    impl SplitReader {
        fn new(data: Vec<u8>, chunk: usize) -> Self {
            Self {
                data,
                offset: 0,
                chunk,
            }
        }
    }

    impl ReadWithTimeout for SplitReader {
        fn read_with_timeout(
            &mut self,
            output: &mut [u8],
            _timeout: Duration,
        ) -> std::io::Result<usize> {
            let available = self.data.len().saturating_sub(self.offset);
            if available == 0 {
                return Ok(0);
            }
            let count = available.min(self.chunk).min(output.len());
            output[..count].copy_from_slice(&self.data[self.offset..self.offset + count]);
            self.offset += count;
            Ok(count)
        }
    }

    struct CrossingReader {
        data: Vec<u8>,
        offset: usize,
        clock: FakeClock,
        step: Duration,
    }

    impl CrossingReader {
        fn new(data: Vec<u8>, clock: FakeClock, step: Duration) -> Self {
            Self {
                data,
                offset: 0,
                clock,
                step,
            }
        }
    }

    impl ReadWithTimeout for CrossingReader {
        fn read_with_timeout(
            &mut self,
            output: &mut [u8],
            _timeout: Duration,
        ) -> std::io::Result<usize> {
            self.clock.advance(self.step);
            let available = self.data.len().saturating_sub(self.offset);
            if available == 0 || output.is_empty() {
                return Ok(0);
            }
            let count = available.min(output.len());
            output[..count].copy_from_slice(&self.data[self.offset..self.offset + count]);
            self.offset += count;
            Ok(count)
        }
    }

    struct TimeoutRecorder {
        data: Vec<u8>,
        offset: usize,
        clock: FakeClock,
        step: Duration,
        timeouts: Vec<Duration>,
    }

    impl TimeoutRecorder {
        fn new(data: Vec<u8>, clock: FakeClock, step: Duration) -> Self {
            Self {
                data,
                offset: 0,
                clock,
                step,
                timeouts: Vec::new(),
            }
        }

        fn timeouts(&self) -> Vec<Duration> {
            self.timeouts.clone()
        }
    }

    impl ReadWithTimeout for TimeoutRecorder {
        fn read_with_timeout(
            &mut self,
            output: &mut [u8],
            timeout: Duration,
        ) -> std::io::Result<usize> {
            self.timeouts.push(timeout);
            self.clock.advance(self.step);
            if self.offset >= self.data.len() || output.is_empty() {
                return Ok(0);
            }
            output[0] = self.data[self.offset];
            self.offset += 1;
            Ok(1)
        }
    }
}
