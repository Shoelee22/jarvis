"""Pinned model downloader: consent-gated, hash-verified, resumable."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

try:
    from .config import DATA_DIR
except ImportError:  # run directly: python sidecar/jarvis/models.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from jarvis.config import DATA_DIR


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def default_manifest_path() -> Path:
    """Where the pinned-model manifest lives.

    Resolution order (works both dev and bundled):
      1. JARVIS_MANIFEST env var (the Tauri shell sets this to the bundled
         models/manifest.json resource when it spawns the sidecar).
      2. Repo-relative models/manifest.json (dev checkout).
    """
    env = os.environ.get("JARVIS_MANIFEST")
    if env and Path(env).exists():
        return Path(env)
    return Path(__file__).resolve().parents[2] / "models" / "manifest.json"


def download(manifest_path: str | None = None, yes: bool = False,
             only: list | tuple | None = None):
    """Download pinned models.

    `only`: optional iterable of manifest entry ids to fetch (e.g.
    ["tts-en", "stt"]); None downloads everything.
    """
    manifest = json.loads(Path(manifest_path or default_manifest_path()).read_text())
    print(manifest["policy"])
    if not yes:
        try:
            ans = input("Download pinned models? [y/N] ").strip().lower()
        except (EOFError, OSError):
            # Non-interactive (e.g. windowed sidecar exe on Windows): treat as declined.
            print("Aborted — JARVIS runs in limited mode without models.")
            return
        if ans != "y":
            print("Aborted — JARVIS runs in limited mode without models.")
            return
    dest = DATA_DIR / "models"
    dest.mkdir(parents=True, exist_ok=True)
    want = set(only) if only else None
    for m in manifest["models"]:
        if want is not None and m.get("id") not in want:
            continue
        # Multi-file entries (e.g. a voice = .onnx + .onnx.json, or a
        # whisper model = 4 files). Single-file entries keep the legacy
        # {"file", "url", "sha256"} shape.
        files = m.get("files")
        if not files and m.get("url"):
            files = [{"path": m["file"], "url": m["url"],
                      "sha256": m.get("sha256")}]
        for f in files or []:
            target = dest / f["path"]
            want = f.get("sha256") or ""
            if target.exists() and want and want != "REPLACE_WITH_PINNED_HASH":
                if sha256_of(target) == want:
                    print(f"OK (cached) {m['id']}:{target.name}")
                    continue
            print(f"GET {f['url']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            last_err: Exception | None = None
            for attempt in range(3):
                try:
                    urllib.request.urlretrieve(f["url"], target)
                    last_err = None
                    break
                except Exception as e:  # transient proxy/network failure
                    last_err = e
                    if target.is_file():
                        target.unlink(missing_ok=True)
                    print(f"retry {attempt + 1}/3 after {type(e).__name__}")
            if last_err is not None:
                raise last_err
            if want and want != "REPLACE_WITH_PINNED_HASH":
                assert sha256_of(target) == want, \
                    f"hash mismatch: {m['id']}:{target.name}"
            print(f"OK {m['id']}:{target.name}")
    print("done.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="download",
                    choices=["download"])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--only", nargs="*", default=None,
                    help="only download these manifest entry ids")
    args = ap.parse_args()
    download(args.manifest, args.yes, only=args.only)
