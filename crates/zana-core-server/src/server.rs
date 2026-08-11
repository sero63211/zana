//! Loopback-only ZANA Core HTTP server with bounded lifecycle and CORS.

use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use zana_core::auth;
use zana_core::db::Database;
use zana_core::error::{self, ErrorDetail};

use crate::http::{self, ParseError, Request, Response};

pub const MAX_CONNECTIONS: usize = 8;

const ALLOWED_ORIGINS: [&str; 4] = [
    "http://127.0.0.1",
    "http://localhost",
    "tauri://localhost",
    "https://tauri.localhost",
];
const ALLOWED_METHODS: &str = "GET, POST, PUT, DELETE, OPTIONS";
const ALLOWED_HEADERS: &str = "Authorization, Content-Type";

pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    pub token: String,
    pub version: String,
}

struct Server {
    config: ServerConfig,
    started_at: Instant,
}

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
    version: String,
    python_version: &'static str,
    pid: u32,
    uptime_seconds: f64,
}

#[derive(Serialize)]
struct ErrorEnvelope<'a> {
    error: &'a ErrorDetail,
}

struct ActiveGuard(Arc<AtomicUsize>);

impl Drop for ActiveGuard {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

pub fn run(config: ServerConfig, database: Database) -> Result<(), zana_core::CoreError> {
    validate_bootstrap(&database)?;
    // rusqlite connections are not Sync, so the handle is owned by this
    // function for the whole server lifetime and closed deterministically on
    // exit; it is never placed in the Arc shared by handler threads.
    let _database = database;

    let bind = if config.host == "::1" {
        ("::1", config.port)
    } else {
        ("127.0.0.1", config.port)
    };
    let listener = TcpListener::bind(bind).map_err(|_| zana_core::CoreError::server())?;
    listener
        .set_nonblocking(true)
        .map_err(|_| zana_core::CoreError::server())?;

    let shutdown = Arc::new(AtomicBool::new(false));
    install_signal_handlers(Arc::clone(&shutdown));

    let server = Arc::new(Server {
        config,
        started_at: Instant::now(),
    });
    let active = Arc::new(AtomicUsize::new(0));
    let handles: Arc<Mutex<Vec<thread::JoinHandle<()>>>> = Arc::new(Mutex::new(Vec::new()));

    loop {
        if shutdown.load(Ordering::SeqCst) {
            break;
        }
        match listener.accept() {
            Ok((stream, _)) => {
                let stream = match prepare_accepted_stream(stream) {
                    Ok(stream) => stream,
                    Err(_) => continue,
                };
                if active.fetch_add(1, Ordering::SeqCst) >= MAX_CONNECTIONS {
                    active.fetch_sub(1, Ordering::SeqCst);
                    let _ = write_busy_response(stream);
                    continue;
                }
                let server = Arc::clone(&server);
                let active = Arc::clone(&active);
                let handle = thread::spawn(move || {
                    let _guard = ActiveGuard(active);
                    let _ = server.handle_connection(stream);
                });
                lock(&handles).push(handle);
                prune_finished_handles(&handles);
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(20));
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) if shutdown.load(Ordering::SeqCst) => break,
            Err(_) => thread::sleep(Duration::from_millis(100)),
        }
    }

    drop(listener);
    let mut guard = lock(&handles);
    let pending = std::mem::take(&mut *guard);
    drop(guard);
    for handle in pending {
        let _ = handle.join();
    }
    Ok(())
}

fn validate_bootstrap(database: &Database) -> Result<(), zana_core::CoreError> {
    database.pragma_state()?;
    Ok(())
}

/// Make an accepted stream blocking with bounded read/write timeouts.
///
/// A stream accepted from a nonblocking listener may inherit nonblocking
/// mode on some platforms. Returning an error drops the stream, which closes
/// it safely, before any handler or busy response can observe it.
fn prepare_accepted_stream(stream: TcpStream) -> std::io::Result<TcpStream> {
    stream.set_nonblocking(false)?;
    stream.set_read_timeout(Some(http::CONNECTION_TIMEOUT))?;
    stream.set_write_timeout(Some(http::CONNECTION_TIMEOUT))?;
    Ok(stream)
}

impl Server {
    fn handle_connection(&self, mut stream: TcpStream) -> std::io::Result<()> {
        let request = match http::read_request(&mut stream) {
            Ok(request) => request,
            Err(ParseError::HeadersTooLarge) => {
                return http::write_response(
                    &mut stream,
                    &error_response(431, error::headers_too_large()),
                );
            }
            Err(ParseError::PayloadTooLarge) => {
                return http::write_response(
                    &mut stream,
                    &error_response(413, error::payload_too_large()),
                );
            }
            Err(ParseError::Timeout) => {
                return http::write_response(
                    &mut stream,
                    &error_response(408, error::request_timeout()),
                );
            }
            Err(_) => {
                return http::write_response(
                    &mut stream,
                    &error_response(400, error::bad_request()),
                );
            }
        };
        let response = self.handle_request(&request);
        http::write_response(&mut stream, &response)
    }

    fn handle_request(&self, request: &Request) -> Response {
        let cors = cors_headers(request);
        let response = match (request.method.as_str(), request.path.as_str()) {
            ("OPTIONS", _) => self.handle_preflight(request),
            ("GET", "/api/v1/health") => self.handle_health(request),
            (_, "/api/v1/health") => error_response(405, error::method_not_allowed()),
            _ => error_response(404, error::not_found()),
        };
        response.with_headers(cors)
    }

    fn handle_health(&self, request: &Request) -> Response {
        if request.body_len != 0 {
            return error_response(400, error::bad_request());
        }
        if !auth::verify_token(&self.config.token, request.authorization()) {
            return error_response(401, error::unauthorized());
        }
        let health = HealthResponse {
            status: "ok",
            version: self.config.version.clone(),
            python_version: "not-required",
            pid: std::process::id(),
            uptime_seconds: (self.started_at.elapsed().as_secs_f64() * 1000.0).round() / 1000.0,
        };
        match serde_json::to_vec(&health) {
            Ok(body) => Response::json(200, &body),
            Err(_) => error_response(500, error::internal()),
        }
    }

    fn handle_preflight(&self, request: &Request) -> Response {
        let origin = request.origin();
        let requested_method = request.header("access-control-request-method");
        let origin_allowed = origin.is_some_and(|origin| ALLOWED_ORIGINS.contains(&origin));
        let method_allowed = requested_method
            .is_some_and(|method| ALLOWED_METHODS.split(", ").any(|allowed| allowed == method));
        if origin_allowed && method_allowed {
            return Response::empty(200)
                .with_header("access-control-allow-methods", ALLOWED_METHODS)
                .with_header("access-control-allow-headers", ALLOWED_HEADERS)
                .with_header("access-control-max-age", "600")
                .with_header(
                    "vary",
                    "Origin, Access-Control-Request-Method, Access-Control-Request-Headers",
                );
        }
        error_response(403, error::cors_disallowed())
    }
}

fn cors_headers(request: &Request) -> Vec<(String, String)> {
    let mut headers: Vec<(String, String)> = Vec::new();
    if let Some(origin) = request.origin().and_then(|origin| {
        ALLOWED_ORIGINS
            .iter()
            .find(|allowed| **allowed == origin)
            .copied()
    }) {
        headers.push(("access-control-allow-origin".to_owned(), origin.to_owned()));
        headers.push((
            "access-control-allow-credentials".to_owned(),
            "true".to_owned(),
        ));
        headers.push(("vary".to_owned(), "Origin".to_owned()));
    }
    headers
}

fn error_response(status: u16, detail: ErrorDetail) -> Response {
    let envelope = ErrorEnvelope { error: &detail };
    match serde_json::to_vec(&envelope) {
        Ok(body) => Response::json(status, &body),
        Err(_) => Response::json(500, br#"{"error":{"code":"INTERNAL_ERROR","message":"An internal error occurred.","details":{},"recoverable":false,"actions":[]}}"#),
    }
}

fn write_busy_response(mut stream: TcpStream) -> std::io::Result<()> {
    http::write_response(
        &mut stream,
        &error_response(503, error::service_unavailable()),
    )
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    match mutex.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    }
}

fn prune_finished_handles(handles: &Mutex<Vec<thread::JoinHandle<()>>>) {
    lock(handles).retain(|handle| !handle.is_finished());
}

#[cfg(unix)]
fn install_signal_handlers(shutdown: Arc<AtomicBool>) {
    let _ = signal_hook::flag::register(signal_hook::consts::SIGINT, Arc::clone(&shutdown));
    let _ = signal_hook::flag::register(signal_hook::consts::SIGTERM, shutdown);
}

#[cfg(not(unix))]
fn install_signal_handlers(_shutdown: Arc<AtomicBool>) {}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use zana_core::db::Database;

    fn test_server(token: &str) -> Server {
        Server {
            config: ServerConfig {
                host: "127.0.0.1".to_owned(),
                port: 0,
                token: token.to_owned(),
                version: "0.1.0".to_owned(),
            },
            started_at: Instant::now(),
        }
    }

    #[test]
    fn bootstrap_validation_uses_accepted_pragmas() {
        let dir =
            std::env::temp_dir().join(format!("zana-core-server-bootstrap-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let db_path = dir.join("db").join("zana.sqlite3");
        let database = Database::open(db_path).expect("opens test db");
        assert!(validate_bootstrap(&database).is_ok());
        database.close();
        let _ = std::fs::remove_dir_all(&dir);
    }

    fn request(method: &str, path: &str, headers: Vec<(&str, &str)>) -> Request {
        Request {
            method: method.to_owned(),
            path: path.to_owned(),
            headers: headers
                .into_iter()
                .map(|(name, value)| (name.to_ascii_lowercase(), value.to_owned()))
                .collect(),
            body_len: 0,
        }
    }

    #[test]
    fn health_requires_bearer_and_returns_exact_envelope() {
        let server = test_server("secret-token");
        let missing = server.handle_request(&request("GET", "/api/v1/health", vec![]));
        assert_eq!(missing.status, 401);
        assert!(String::from_utf8_lossy(&missing.body).contains("\"code\":\"UNAUTHORIZED\""));

        let valid = server.handle_request(&request(
            "GET",
            "/api/v1/health",
            vec![("Authorization", "Bearer secret-token")],
        ));
        assert_eq!(valid.status, 200);
        let body: serde_json::Value = serde_json::from_slice(&valid.body).expect("valid JSON");
        assert_eq!(body["status"], "ok");
        assert_eq!(body["version"], "0.1.0");
        assert_eq!(body["python_version"], "not-required");
        assert!(body["pid"].is_number());
        assert!(body["uptime_seconds"].is_number());
    }

    #[test]
    fn cors_preflight_and_disallowed_origin() {
        let server = test_server("secret-token");
        let allowed = server.handle_request(&request(
            "OPTIONS",
            "/api/v1/health",
            vec![
                ("Origin", "tauri://localhost"),
                ("Access-Control-Request-Method", "GET"),
                ("Access-Control-Request-Headers", "authorization"),
            ],
        ));
        assert_eq!(allowed.status, 200);
        let headers = allowed.headers;
        assert!(headers.iter().any(|(name, value)| {
            name == "access-control-allow-origin" && value == "tauri://localhost"
        }));
        assert!(headers
            .iter()
            .any(|(name, _)| name == "access-control-allow-methods"));

        let disallowed = server.handle_request(&request(
            "OPTIONS",
            "/api/v1/health",
            vec![
                ("Origin", "https://evil.example"),
                ("Access-Control-Request-Method", "GET"),
            ],
        ));
        assert_eq!(disallowed.status, 403);
    }

    #[test]
    fn unknown_routes_use_canonical_error_envelope() {
        let server = test_server("secret-token");
        let not_found = server.handle_request(&request("GET", "/api/v1/unknown", vec![]));
        assert_eq!(not_found.status, 404);
        let body: serde_json::Value = serde_json::from_slice(&not_found.body).expect("valid JSON");
        assert_eq!(body["error"]["code"], "NOT_FOUND");

        let not_allowed = server.handle_request(&request(
            "POST",
            "/api/v1/health",
            vec![("Authorization", "Bearer secret-token")],
        ));
        assert_eq!(not_allowed.status, 405);
        let body: serde_json::Value =
            serde_json::from_slice(&not_allowed.body).expect("valid JSON");
        assert_eq!(body["error"]["code"], "METHOD_NOT_ALLOWED");
    }

    #[test]
    fn allowed_origin_gets_cors_on_regular_health_response() {
        let server = test_server("secret-token");
        let response = server.handle_request(&request(
            "GET",
            "/api/v1/health",
            vec![
                ("Authorization", "Bearer secret-token"),
                ("Origin", "http://127.0.0.1"),
            ],
        ));
        assert_eq!(response.status, 200);
        assert!(response.headers.iter().any(|(name, value)| {
            name == "access-control-allow-origin" && value == "http://127.0.0.1"
        }));
    }

    #[test]
    fn delayed_client_receives_authenticated_health_response() {
        let server = test_server("secret-token");
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("binds test listener");
        listener
            .set_nonblocking(true)
            .expect("nonblocking listener");
        let port = listener.local_addr().expect("local address").port();

        let server_thread = thread::spawn(move || {
            let (stream, _) = loop {
                match listener.accept() {
                    Ok(accepted) => break accepted,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("accept failed: {error}"),
                }
            };
            let stream = prepare_accepted_stream(stream).expect("accepted stream is blocking");
            server
                .handle_connection(stream)
                .expect("handles connection");
        });

        let client = TcpStream::connect(("127.0.0.1", port)).expect("connects");
        // Connect first, then send after a bounded delay. The server must wait
        // for the request instead of failing immediately on WouldBlock.
        thread::sleep(Duration::from_millis(150));
        let mut client = client;
        client
            .write_all(
                b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer secret-token\r\n\r\n",
            )
            .expect("writes request");
        let mut response = String::new();
        client
            .read_to_string(&mut response)
            .expect("reads response");
        server_thread.join().expect("server thread completes");

        assert!(
            response.starts_with("HTTP/1.1 200 OK\r\n"),
            "unexpected response: {response:?}"
        );
        assert!(response.contains("\"status\":\"ok\""));
        assert!(response.contains("\"python_version\":\"not-required\""));
    }
}
