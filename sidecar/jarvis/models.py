"""Pinned model downloader: consent-gated, hash-verified, resumable."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path

from .config import DATA_DIR


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


def download(manifest_path: str | None = None, yes: bool = False):
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
    for m in manifest["models"]:
        target = dest / m["file"]
        if target.exists() and m["sha256"] != "REPLACE_WITH_PINNED_HASH":
            if sha256_of(target) == m["sha256"]:
                print(f"OK (cached) {m['id']}")
                continue
        print(f"GET {m['url']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(m["url"], target)
        if m["sha256"] != "REPLACE_WITH_PINNED_HASH":
            assert sha256_of(target) == m["sha256"], f"hash mismatch: {m['id']}"
        print(f"OK {m['id']} ({m['size_gb']} GB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    download(args.manifest, args.yes)
