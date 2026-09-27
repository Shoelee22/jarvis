//! Audio capture (cpal), wake-word loop (openWakeWord), camera capture stub.
//! Camera-active state ALWAYS drives the tray icon + on-screen dot.

use std::sync::atomic::{AtomicBool, Ordering};

static CAMERA_ON: AtomicBool = AtomicBool::new(false);

pub fn wake_word_loop() {
    // Production: load openWakeWord ONNX (~50MB), run on mic stream,
    // POST /v1/listen/start {mode:"wakeword"} on detection.
    loop {
        std::thread::sleep(std::time::Duration::from_secs(60));
    }
}

pub fn stop_camera() {
    CAMERA_ON.store(false, Ordering::SeqCst);
    // Production: also tear down the capture thread and notify sidecar
    // so in-flight vision jobs are cancelled.
}

pub fn camera_active() -> bool {
    CAMERA_ON.load(Ordering::SeqCst)
}
