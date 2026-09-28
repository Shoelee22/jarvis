//! Minimal sidecar HTTP client + supervisor.

use std::path::PathBuf;
use std::process::{Child, Command};
use std::time::Duration;

use tauri::{AppHandle, Manager};

use super::SIDECAR_URL;

/// Per-user JARVIS data dir. Mirrors sidecar/jarvis/config.py exactly:
/// JARVIS_DATA override, then %APPDATA%/ai.jarvis.shell on Windows,
/// ~/.jarvis everywhere else.
pub fn jarvis_data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("JARVIS_DATA") {
        return PathBuf::from(dir);
    }
    if cfg!(windows) {
        if let Ok(appdata) = std::env::var("APPDATA") {
            return PathBuf::from(appdata).join("ai.jarvis.shell");
        }
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".jarvis")
}

pub fn token_path() -> PathBuf {
    jarvis_data_dir().join(".token")
}

pub fn listen_start(mode: &str) -> Result<String, String> {
    let body = serde_json::json!({ "mode": mode });
    let token = sidecar_token();
    let resp: serde_json::Value = reqwest::blocking::Client::new()
        .post(format!("{SIDECAR_URL}/v1/listen/start"))
        .header("Authorization", format!("Bearer {token}"))
        .json(&body)
        .send()
        .map_err(|e| format!("sidecar unreachable: {e}"))?
        .json()
        .map_err(|e| e.to_string())?;
    Ok(resp["session_id"].as_str().unwrap_or("").to_string())
}

fn sidecar_token() -> String {
    std::fs::read_to_string(token_path())
        .unwrap_or_default()
        .trim()
        .to_string()
}

fn spawn_sidecar(manifest: Option<&std::path::Path>) -> Option<Child> {
    let exe = std::env::current_exe().ok()?;
    // Tauri `bundle.externalBin` installs the sidecar next to the app exe,
    // with the target-triple suffix stripped (jarvis-sidecar.exe on Windows).
    let mut sidecar = exe.parent()?.join("jarvis-sidecar");
    if cfg!(windows) {
        sidecar.set_extension("exe");
    }
    let mut cmd = Command::new(sidecar);
    if let Some(m) = manifest {
        // Tell the sidecar where the bundled models/manifest.json resource is.
        cmd.env("JARVIS_MANIFEST", m);
    }
    cmd.spawn().ok()
}

/// Path of the bundled models/manifest.json resource, if present.
/// In dev (cargo tauri dev) there is no bundle, so this is None and the
/// sidecar falls back to the repo-relative manifest.
fn bundled_manifest(app: &AppHandle) -> Option<PathBuf> {
    let p = app
        .path()
        .resource_dir()
        .ok()?
        .join("models")
        .join("manifest.json");
    p.exists().then_some(p)
}

/// Restart loop: the shell outlives the brain, always.
pub fn supervise_sidecar(app: &AppHandle) {
    let manifest = bundled_manifest(app);
    loop {
        let healthy = reqwest::blocking::get(format!("{SIDECAR_URL}/v1/health"))
            .map(|r| r.status().is_success())
            .unwrap_or(false);
        if !healthy {
            let _ = spawn_sidecar(manifest.as_deref());
        }
        std::thread::sleep(Duration::from_secs(5));
    }
}
