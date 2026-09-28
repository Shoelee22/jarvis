"""Local LLM server manager: downloads, verifies, spawns, and supervises
llama-server.exe (from llama.cpp releases) running a pinned GGUF model.

The brain runs as a child process speaking OpenAI-compatible HTTP on
127.0.0.1. The sidecar supervises it: restart on crash, never silently dead.
All downloads are SHA-256 verified against models/manifest.json.
"""
from __future__ import annotations
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

# Fixed loopback port for the brain. Unlikely to collide; we probe before bind.
LLM_PORT = 8823
# How long to wait for /health after spawn before declaring failure.
STARTUP_TIMEOUT_S = 120


def _manifest() -> dict:
    from .models import default_manifest_path
    return json.loads(Path(default_manifest_path()).read_text(encoding="utf-8"))


def _entry(entry_id: str) -> dict | None:
    for m in _manifest().get("models", []):
        if m.get("id") == entry_id:
            return m
    return None


def _data_dir() -> Path:
    from .config import DATA_DIR
    return Path(DATA_DIR)


def bin_dir() -> Path:
    d = _data_dir() / "bin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def server_exe() -> Path:
    return bin_dir() / "llama-server.exe"


def model_path() -> Path | None:
    e = _entry("llm")
    if not e:
        return None
    files = e.get("files") or []
    if not files:
        return None
    p = _data_dir() / "models" / files[0]["path"]
    return p if p.exists() else None


def _sha256_of(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_file(url: str, dest: Path, expected_sha256: str | None,
                   progress_cb=None) -> None:
    """Download with resume + SHA-256 verification."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Resume: if partial file exists, try Range request.
    existing = dest.stat().st_size if dest.exists() else 0
    req = urllib.request.Request(url)
    if existing:
        req.add_header("Range", f"bytes={existing}-")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except Exception as e:
        raise RuntimeError(f"download failed: {e}")
    total = int(resp.headers.get("Content-Length", "0") or "0") + existing
    mode = "ab" if existing and resp.status == 206 else "wb"
    if mode == "wb":
        existing = 0
    downloaded = existing
    with open(dest, mode) as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if progress_cb and total:
                progress_cb(downloaded / total)
    if expected_sha256 and expected_sha256 != "REPLACE_WITH_PINNED_HASH":
        actual = _sha256_of(dest)
        if actual != expected_sha256:
            dest.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA-256 mismatch for {dest.name}: expected "
                f"{expected_sha256[:16]}…, got {actual[:16]}…")


class LlmServer:
    """Supervises llama-server.exe + the pinned GGUF model."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._status: str = "unknown"  # unknown|missing|downloading|loading|ready|failed
        self._detail: str = ""
        self._progress: float = 0.0
        self._wanted = threading.Event()  # set when the user wants the brain

    # ---- public status -------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            st, detail, prog = self._status, self._detail, self._progress
        # If we think we're ready, verify the process is actually alive.
        if st == "ready" and not self._alive():
            self._set("failed", "llama-server process died", 0.0)
            st = "failed"
        return {
            "status": st,
            "detail": detail,
            "progress": round(prog, 3),
            "port": LLM_PORT,
            "model": (_entry("llm") or {}).get("family", "unknown"),
        }

    def _set(self, status: str, detail: str = "", progress: float = 0.0):
        with self._lock:
            self._status, self._detail, self._progress = status, detail, progress

    def _alive(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    # ---- setup ---------------------------------------------------------
    def ensure_ready(self, progress_cb=None) -> dict:
        """Download (if needed) and start the brain. Returns status dict."""
        self._wanted.set()
        exe = server_exe()
        if not exe.exists():
            self._set("downloading", "fetching llama-server", 0.0)
            try:
                self._download_server(progress_cb)
            except Exception as e:
                self._set("failed", f"llama-server download failed: {e}", 0.0)
                return self.status()
        mp = model_path()
        if not mp:
            # Download the GGUF model (2.5 GB — progress-tracked).
            self._set("downloading", "fetching chat model (one-time, ~2.5 GB)", 0.0)
            try:
                self._download_model(progress_cb)
            except Exception as e:
                self._set("failed", f"model download failed: {e}", 0.0)
                return self.status()
            mp = model_path()
        if not mp:
            self._set("missing", "chat model not downloaded — Settings → Models → Download", 0.0)
            return self.status()
        if not self._alive():
            self._set("loading", "starting local brain", 0.0)
            try:
                self._spawn(mp)
            except Exception as e:
                self._set("failed", f"could not start llama-server: {e}", 0.0)
                return self.status()
            if not self._wait_ready():
                self._set("failed", "llama-server did not become ready in time", 0.0)
                return self.status()
        self._set("ready", f"local brain ready ({mp.name})", 1.0)
        return self.status()

    def _download_model(self, progress_cb=None):
        e = _entry("llm")
        if not e or not e.get("files"):
            raise RuntimeError("llm not pinned in manifest")
        f = e["files"][0]
        dest = _data_dir() / "models" / f["path"]
        if dest.exists():
            return
        def _prog(p):
            self._set("downloading",
                      f"fetching chat model {p*100:.0f}% (one-time, ~2.5 GB)", p)
            if progress_cb:
                progress_cb(p)
        _download_file(f["url"], dest, f.get("sha256"), progress_cb=_prog)

    def _download_server(self, progress_cb=None):
        e = _entry("llm-server")
        if not e or not e.get("files"):
            raise RuntimeError("llm-server not pinned in manifest")
        f = e["files"][0]
        tmp = bin_dir() / "_llama_tmp.zip"
        _download_file(f["url"], tmp, f.get("sha256"),
                       progress_cb=lambda p: (self._set("downloading", "fetching llama-server", p * 0.9),
                                              progress_cb(p * 0.9) if progress_cb else None))
        # Extract only what we need.
        with zipfile.ZipFile(tmp) as z:
            for name in z.namelist():
                base = os.path.basename(name).lower()
                if base in ("llama-server.exe", "llama.dll", "ggml.dll",
                            "ggml-base.dll", "ggml-cpu.dll"):
                    target = bin_dir() / os.path.basename(name)
                    with z.open(name) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        tmp.unlink(missing_ok=True)
        if not server_exe().exists():
            raise RuntimeError("llama-server.exe not found in release zip")

    def _spawn(self, model: Path):
        exe = str(server_exe())
        cmd = [
            exe,
            "-m", str(model),
            "-c", "8192",           # context window
            "--port", str(LLM_PORT),
            "--host", "127.0.0.1",
            "-n", "1024",           # max tokens per response
            "--jinja",              # enable tool-calling templates
        ]
        log.info("spawning brain: %s", " ".join(cmd[:4]) + " …")
        # Detached, no console window on Windows.
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

    def _wait_ready(self) -> bool:
        deadline = time.time() + STARTUP_TIMEOUT_S
        url = f"http://127.0.0.1:{LLM_PORT}/health"
        while time.time() < deadline:
            if not self._alive():
                return False
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    if r.status == 200:
                        return True
            except Exception:
                pass
            time.sleep(2)
        return False

    def stop(self):
        with self._lock:
            p, self._proc = self._proc, None
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=10)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


# Singleton: one brain per sidecar process.
_llm_server: LlmServer | None = None


def get_llm_server() -> LlmServer:
    global _llm_server
    if _llm_server is None:
        _llm_server = LlmServer()
    return _llm_server
