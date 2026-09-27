"""Filesystem tools, jailed to granted roots."""
from __future__ import annotations
from pathlib import Path

MAX_READ = 200_000  # chars

ALLOWED_ROOTS = [Path.home()]


def _resolve(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    if not any(str(p).startswith(str(r.resolve())) for r in ALLOWED_ROOTS):
        raise PermissionError(f"path outside granted roots: {path}")
    return p


def read(args: dict) -> dict:
    p = _resolve(args["path"])
    text = p.read_text(errors="replace")
    truncated = len(text) > MAX_READ
    return {"path": str(p), "content": text[:MAX_READ], "truncated": truncated}


def write(args: dict) -> dict:
    p = _resolve(args["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args["content"])
    return {"path": str(p), "bytes": len(args["content"])}


def delete(args: dict) -> dict:
    p = _resolve(args["path"])
    p.unlink()
    return {"deleted": str(p)}


def list_dir(args: dict) -> dict:
    p = _resolve(args.get("path", str(Path.home())))
    return {"path": str(p), "entries": sorted(x.name for x in p.iterdir())}


def search(args: dict) -> dict:
    root = _resolve(args.get("root", str(Path.home())))
    pattern, hits = args["pattern"].lower(), []
    for f in root.rglob("*"):
        if len(hits) >= 50:
            break
        if f.is_file() and pattern in f.name.lower():
            hits.append(str(f))
    return {"hits": hits}
