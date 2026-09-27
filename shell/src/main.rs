//! JARVIS shell: tray, global hotkey, mic/camera capture, chat window.
//! The brain lives in the Python sidecar (localhost:8765). This process owns
//! the OS surface and the visible mic/camera indicators. A sidecar crash must
//! never kill this process.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

mod ipc;
mod audio;

const SIDECAR_URL: &str = "http://127.0.0.1:8765";

#[tauri::command]
fn push_to_talk_start() -> Result<String, String> {
    // Opens an audio session with the sidecar; shell streams PCM over WS.
    Ok(ipc::listen_start("push_to_talk").map_err(|e| e.to_string())?)
}

#[tauri::command]
fn camera_kill() -> String {
    // Software camera kill-switch: stops capture thread + tells sidecar.
    audio::stop_camera();
    "camera off".into()
}

#[tauri::command]
fn get_sidecar_token() -> Result<String, String> {
    // The chat UI needs the sidecar bearer token but cannot read files.
    // Token file is created by the sidecar on first run (0600 on unix).
    std::fs::read_to_string(ipc::token_path())
        .map(|s| s.trim().to_string())
        .map_err(|e| e.to_string())
}

fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Open JARVIS", true, None::<&str>)?;
    let listen = MenuItem::with_id(app, "listen", "Listen now", true, None::<&str>)?;
    let camkill = MenuItem::with_id(app, "camkill", "Camera OFF", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &listen, &camkill, &quit])?;
    TrayIconBuilder::new()
        .menu(&menu)
        .tooltip("JARVIS — local brain idle")
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => {
                if let Some(w) = app.get_webview_window("chat") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
            "listen" => {
                let _ = push_to_talk_start();
            }
            "camkill" => {
                camera_kill();
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        // MacosLauncher::LaunchAgent is the canonical arg on ALL desktop
        // platforms (per the plugin's own docs): it is only honoured on
        // macOS; on Windows the plugin uses the Registry Run key, on Linux
        // a .desktop file. No per-OS gating needed.
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .invoke_handler(tauri::generate_handler![push_to_talk_start, camera_kill, get_sidecar_token])
        .setup(|app| {
            build_tray(app)?;
            // Global hotkey Ctrl+Shift+J → push-to-talk (remappable in Settings).
            #[cfg(desktop)]
            {
                use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};
                let shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyJ);
                let _ = app.global_shortcut().register(shortcut);
            }
            // Wake-word listener runs here (openWakeWord ONNX, ~50MB) so the
            // sidecar can sleep until "Jarvis" is heard.
            std::thread::spawn(audio::wake_word_loop);
            // Supervise the sidecar: restart it if it dies, never exit with it.
            // The handle lets the supervisor find the bundled model manifest.
            let handle = app.handle().clone();
            std::thread::spawn(move || ipc::supervise_sidecar(&handle));
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("JARVIS shell failed to start");
}
