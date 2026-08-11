//! Deterministic platform data-root resolution and safe child derivation.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use crate::error::CoreError;

pub const APP_NAME: &str = "zana";
pub const MAX_CHILD_DEPTH: usize = 16;
pub const MAX_COMPONENT_LENGTH: usize = 255;

const DATABASE_COMPONENTS: [&str; 2] = ["db", "zana.sqlite3"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlatformKind {
    Macos,
    Linux,
    Windows,
    Unsupported,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlatformPaths {
    pub data_root: PathBuf,
    pub database_path: PathBuf,
}

/// Resolve the OS data root for ZANA without mutating the filesystem.
///
/// The layout matches the accepted Python `platformdirs` contract: macOS
/// `~/Library/Application Support/zana`, Linux `$XDG_DATA_HOME/zana` (or
/// `~/.local/share/zana`), and Windows `%LOCALAPPDATA%\zana\zana` because
/// `platformdirs.user_data_dir("zana")` defaults appauthor to appname.
pub fn data_root_for(
    home: Option<PathBuf>,
    xdg_data_home: Option<PathBuf>,
    local_app_data: Option<PathBuf>,
) -> Result<PathBuf, CoreError> {
    data_root_for_platform(
        current_platform_kind(),
        home.as_deref(),
        xdg_data_home.as_deref(),
        local_app_data.as_deref(),
    )
}

/// Pure per-platform data-root derivation, testable on any host.
pub fn data_root_for_platform(
    platform: PlatformKind,
    home: Option<&Path>,
    xdg_data_home: Option<&Path>,
    local_app_data: Option<&Path>,
) -> Result<PathBuf, CoreError> {
    match platform {
        PlatformKind::Macos => {
            let home = home.ok_or_else(CoreError::data_root)?;
            Ok(home
                .join("Library")
                .join("Application Support")
                .join(APP_NAME))
        }
        PlatformKind::Linux => {
            let base = xdg_data_home
                .map(Path::to_path_buf)
                .or_else(|| home.map(|home| home.join(".local").join("share")))
                .ok_or_else(CoreError::data_root)?;
            Ok(base.join(APP_NAME))
        }
        PlatformKind::Windows => {
            let base = local_app_data.ok_or_else(CoreError::data_root)?;
            Ok(base.join(APP_NAME).join(APP_NAME))
        }
        PlatformKind::Unsupported => Err(CoreError::data_root()),
    }
}

#[cfg(target_os = "macos")]
fn current_platform_kind() -> PlatformKind {
    PlatformKind::Macos
}

#[cfg(all(unix, not(target_os = "macos")))]
fn current_platform_kind() -> PlatformKind {
    PlatformKind::Linux
}

#[cfg(windows)]
fn current_platform_kind() -> PlatformKind {
    PlatformKind::Windows
}

#[cfg(not(any(target_os = "macos", all(unix, not(target_os = "macos")), windows)))]
fn current_platform_kind() -> PlatformKind {
    PlatformKind::Unsupported
}

pub fn resolve_data_root() -> Result<PathBuf, CoreError> {
    data_root_for(
        env::var_os("HOME").map(PathBuf::from),
        env::var_os("XDG_DATA_HOME").map(PathBuf::from),
        env::var_os("LOCALAPPDATA").map(PathBuf::from),
    )
}

pub fn derive_child(root: &Path, components: &[&str]) -> Result<PathBuf, CoreError> {
    if !root.is_absolute() {
        return Err(CoreError::data_root());
    }
    if components.is_empty() {
        return Err(CoreError::data_root());
    }
    if components.len() > MAX_CHILD_DEPTH {
        return Err(CoreError::data_root());
    }
    for component in components {
        if component.is_empty()
            || *component == "."
            || *component == ".."
            || component.contains('/')
            || component.contains('\\')
            || component.len() > MAX_COMPONENT_LENGTH
        {
            return Err(CoreError::data_root());
        }
    }

    let mut candidate = root.to_path_buf();
    for component in components {
        candidate.push(component);
    }
    if !candidate.starts_with(root) {
        return Err(CoreError::data_root());
    }
    Ok(candidate)
}

pub fn resolve_platform_paths() -> Result<PlatformPaths, CoreError> {
    let data_root = resolve_data_root()?;
    let database_path = derive_child(&data_root, &DATABASE_COMPONENTS)?;
    Ok(PlatformPaths {
        data_root,
        database_path,
    })
}

/// Validate and prepare the exact data root and database parent directory.
///
/// The root is validated before any mutation, symlinked roots and database
/// files fail closed, and the created database directory is re-checked against
/// the canonicalized root so a parent symlink cannot smuggle writes outside it.
pub fn prepare_data_root(paths: &PlatformPaths) -> Result<(), CoreError> {
    validate_root(&paths.data_root)?;

    if symlink_metadata(&paths.data_root)?.is_some_and(|metadata| metadata.file_type().is_symlink())
    {
        return Err(CoreError::data_root());
    }
    if symlink_metadata(&paths.database_path)?
        .is_some_and(|metadata| metadata.file_type().is_symlink())
    {
        return Err(CoreError::data_root());
    }

    fs::create_dir_all(&paths.data_root).map_err(|_| CoreError::data_root())?;
    let db_dir = paths
        .database_path
        .parent()
        .ok_or_else(CoreError::data_root)?;
    fs::create_dir_all(db_dir).map_err(|_| CoreError::data_root())?;

    let canonical_root = fs::canonicalize(&paths.data_root).map_err(|_| CoreError::data_root())?;
    let canonical_db_dir = fs::canonicalize(db_dir).map_err(|_| CoreError::data_root())?;
    if !canonical_db_dir.starts_with(&canonical_root) {
        return Err(CoreError::data_root());
    }
    Ok(())
}

/// Metadata for an exact path, or `None` when the path does not exist yet.
///
/// Any other metadata failure fails closed: an unreadable path must never be
/// treated as a missing path and then created through an unsafe location.
fn symlink_metadata(path: &Path) -> Result<Option<fs::Metadata>, CoreError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => Ok(Some(metadata)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(CoreError::data_root()),
    }
}

fn validate_root(root: &Path) -> Result<(), CoreError> {
    if !root.is_absolute()
        || root == Path::new("/")
        || root.to_string_lossy().contains('\0')
        || env::var_os("HOME").is_some_and(|home| Path::new(&home) == root)
    {
        return Err(CoreError::data_root());
    }
    if let Ok(cwd) = env::current_dir() {
        if cwd == root {
            return Err(CoreError::data_root());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mac_data_root_matches_accepted_layout() {
        let root =
            data_root_for(Some(PathBuf::from("/tmp/zana-home")), None, None).expect("resolves");
        #[cfg(target_os = "macos")]
        assert_eq!(
            root,
            PathBuf::from("/tmp/zana-home/Library/Application Support/zana")
        );
        #[cfg(all(unix, not(target_os = "macos")))]
        assert_eq!(root, PathBuf::from("/tmp/zana-home/.local/share/zana"));
    }

    #[test]
    fn windows_data_root_matches_platformdirs_nested_layout() {
        let root = data_root_for_platform(
            PlatformKind::Windows,
            None,
            None,
            Some(Path::new(r"C:\Users\Zana\AppData\Local")),
        )
        .expect("resolves");
        assert_eq!(
            root,
            Path::new(r"C:\Users\Zana\AppData\Local")
                .join(APP_NAME)
                .join(APP_NAME)
        );
    }

    #[test]
    fn database_path_is_derived_safely() {
        let paths = PlatformPaths {
            data_root: PathBuf::from("/tmp/zana"),
            database_path: derive_child(Path::new("/tmp/zana"), &DATABASE_COMPONENTS)
                .expect("derives"),
        };
        assert_eq!(
            paths.database_path,
            PathBuf::from("/tmp/zana/db/zana.sqlite3")
        );
    }

    #[test]
    fn unsafe_child_components_fail_closed() {
        assert!(derive_child(Path::new("/tmp"), &[".."]).is_err());
        assert!(derive_child(Path::new("/tmp"), &["a/b"]).is_err());
        assert!(derive_child(Path::new("/tmp"), &[""]).is_err());
        assert!(derive_child(Path::new("relative"), &["db"]).is_err());
    }

    #[test]
    fn missing_metadata_is_represented_as_none() {
        let missing =
            std::env::temp_dir().join(format!("zana-platform-missing-{}", std::process::id()));
        let _ = std::fs::remove_file(&missing);
        assert!(symlink_metadata(&missing)
            .expect("none for missing")
            .is_none());
    }

    #[test]
    fn symlinked_data_root_fails_closed_without_mutation() {
        #[cfg(unix)]
        {
            let base =
                std::env::temp_dir().join(format!("zana-platform-symlink-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&base);
            std::fs::create_dir_all(base.join("real")).expect("creates real root");
            std::os::unix::fs::symlink(base.join("real"), base.join("linked"))
                .expect("creates link");
            let paths = PlatformPaths {
                data_root: base.join("linked"),
                database_path: base.join("linked").join("db").join("zana.sqlite3"),
            };
            assert!(prepare_data_root(&paths).is_err());
            assert!(!base.join("linked").join("db").exists());
            let _ = std::fs::remove_dir_all(base);
        }
    }
}
