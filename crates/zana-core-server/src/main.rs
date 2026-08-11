//! ZANA Core Rust sidecar entry point.

mod http;
mod server;

use std::env;
use std::process::ExitCode;

use zana_core::auth;
use zana_core::db::Database;
use zana_core::platform;
use zana_core::CORE_VERSION;

use crate::server::ServerConfig;

fn usage() -> &'static str {
    "Usage: zana-core serve --host 127.0.0.1 --port <port> [--token <token>]"
}

struct CliArgs {
    host: String,
    port: u16,
    token: Option<String>,
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.iter().any(|arg| arg == "--help" || arg == "-h") {
        println!("{}", usage());
        return ExitCode::SUCCESS;
    }

    let cli = match parse_cli(&args) {
        Ok(cli) => cli,
        Err(message) => {
            eprintln!("error: {message}");
            return ExitCode::FAILURE;
        }
    };

    let token = resolve_launch_token(cli.token.clone(), env::var("ZANA_CORE_TOKEN").ok());
    let Some(token) = token else {
        eprintln!(
            "error: The launch token must be non-empty, contain no whitespace, and be within the accepted size bound."
        );
        return ExitCode::FAILURE;
    };

    let paths = match platform::resolve_platform_paths() {
        Ok(paths) => paths,
        Err(_) => {
            eprintln!("error: ZANA Core could not resolve its data directory.");
            return ExitCode::FAILURE;
        }
    };
    match platform::prepare_data_root(&paths) {
        Ok(()) => {}
        Err(_) => {
            eprintln!("error: ZANA Core could not initialize its data directory.");
            return ExitCode::FAILURE;
        }
    }
    let database = match Database::open(paths.database_path) {
        Ok(database) => database,
        Err(_) => {
            eprintln!("error: ZANA Core could not initialize its database.");
            return ExitCode::FAILURE;
        }
    };

    let config = ServerConfig {
        host: cli.host,
        port: cli.port,
        token,
        version: CORE_VERSION.to_owned(),
    };
    match server::run(config, database) {
        Ok(()) => ExitCode::SUCCESS,
        Err(_) => {
            eprintln!("error: ZANA Core stopped unexpectedly.");
            ExitCode::FAILURE
        }
    }
}

/// Resolve and validate the launch token before any filesystem or server
/// work. The predicate is the same one used for request authentication.
fn resolve_launch_token(cli_token: Option<String>, env_token: Option<String>) -> Option<String> {
    let token = cli_token.or(env_token)?;
    auth::valid_launch_token(&token).then_some(token)
}

fn parse_cli(args: &[String]) -> Result<CliArgs, String> {
    let mut iter = args.iter();
    let command = iter.next().ok_or_else(|| usage().to_owned())?;
    if command != "serve" {
        return Err(format!(
            "unknown command '{command}'; try 'zana-core --help'"
        ));
    }

    let mut host: Option<String> = None;
    let mut port: Option<u16> = None;
    let mut token: Option<String> = None;
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--host" => {
                host = Some(
                    iter.next()
                        .ok_or_else(|| "missing value for --host".to_owned())?
                        .clone(),
                );
            }
            "--port" => {
                let raw = iter
                    .next()
                    .ok_or_else(|| "missing value for --port".to_owned())?;
                port = Some(parse_port(raw)?);
            }
            "--token" => {
                token = Some(
                    iter.next()
                        .ok_or_else(|| "missing value for --token".to_owned())?
                        .clone(),
                );
            }
            other => return Err(format!("unknown option '{other}'")),
        }
    }

    let host = host.ok_or_else(|| "missing required option --host".to_owned())?;
    let port = port.ok_or_else(|| "missing required option --port".to_owned())?;
    validate_host(&host)?;
    Ok(CliArgs { host, port, token })
}

fn parse_port(raw: &str) -> Result<u16, String> {
    let port: u16 = raw
        .parse()
        .map_err(|_| "port must be a numeric value between 1 and 65535".to_owned())?;
    if port == 0 {
        return Err("port must be a numeric value between 1 and 65535".to_owned());
    }
    Ok(port)
}

fn validate_host(host: &str) -> Result<(), String> {
    if matches!(host, "127.0.0.1" | "localhost" | "::1") {
        Ok(())
    } else {
        Err("host must be a loopback address (127.0.0.1, localhost, or ::1)".to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| value.to_string()).collect()
    }

    #[test]
    fn parses_serve_cli() {
        let cli = parse_cli(&args(&[
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
            "--token",
            "secret",
        ]))
        .expect("parses");
        assert_eq!(cli.host, "127.0.0.1");
        assert_eq!(cli.port, 8080);
        assert_eq!(cli.token.as_deref(), Some("secret"));
    }

    #[test]
    fn rejects_non_loopback_host() {
        assert!(parse_cli(&args(&["serve", "--host", "0.0.0.0", "--port", "8080"])).is_err());
    }

    #[test]
    fn rejects_invalid_ports() {
        assert!(parse_cli(&args(&["serve", "--host", "127.0.0.1", "--port", "0"])).is_err());
        assert!(parse_cli(&args(&["serve", "--host", "127.0.0.1", "--port", "99999"])).is_err());
        assert!(parse_cli(&args(&["serve", "--host", "127.0.0.1", "--port", "abc"])).is_err());
    }

    #[test]
    fn rejects_missing_required_options() {
        assert!(parse_cli(&args(&["serve"])).is_err());
        assert!(parse_cli(&args(&["serve", "--host", "127.0.0.1"])).is_err());
        assert!(parse_cli(&args(&[
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
            "--token"
        ]))
        .is_err());
    }

    #[test]
    fn resolve_launch_token_accepts_only_valid_tokens() {
        assert_eq!(
            resolve_launch_token(Some("secret".to_owned()), None).as_deref(),
            Some("secret")
        );
        assert!(resolve_launch_token(None, None).is_none());
        assert!(resolve_launch_token(Some(String::new()), None).is_none());
        assert!(resolve_launch_token(Some("has space".to_owned()), None).is_none());
        assert!(resolve_launch_token(None, Some(" leading".to_owned())).is_none());
        assert!(resolve_launch_token(Some("a".repeat(auth::MAX_TOKEN_BYTES + 1)), None).is_none());
        assert_eq!(
            resolve_launch_token(Some("cli".to_owned()), Some("env".to_owned())).as_deref(),
            Some("cli")
        );
    }
}
