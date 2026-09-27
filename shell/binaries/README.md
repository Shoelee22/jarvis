# shell/binaries/

This directory holds the **compiled Python sidecar** for the Tauri bundle.
It is gitignored — binaries are produced by CI, never committed.

## What goes here

Exactly one file per target platform, named with the Tauri external-binary
convention (`<name>-<rust-target-triple>[.exe]`):

| File                                              | Built on       |
|---------------------------------------------------|----------------|
| `jarvis-sidecar-x86_64-pc-windows-msvc.exe`       | windows-latest |
| `jarvis-sidecar-x86_64-apple-darwin`              | macos-latest   |
| `jarvis-sidecar-x86_64-unknown-linux-gnu`         | ubuntu-latest  |

At install time Tauri strips the triple suffix and places the binary next to
the app executable (`jarvis-sidecar.exe` beside `JARVIS.exe` on Windows).
The shell spawns it from there — see `shell/src/ipc.rs::spawn_sidecar`.

## How it gets here

**CI (normal path):** `.github/workflows/build-installers.yml` runs
`pyinstaller packaging/sidecar.spec` on each runner, renames
`dist/jarvis-sidecar[.exe]` with the target triple, and drops it here before
`cargo tauri build` runs. You never touch this directory by hand.

**Local build (Windows PC):**

```powershell
pip install pyinstaller fastapi "uvicorn[standard]" pyyaml websockets
pyinstaller packaging/sidecar.spec          # run from the repo root
Copy-Item dist\jarvis-sidecar.exe shell\binaries\jarvis-sidecar-x86_64-pc-windows-msvc.exe
cd shell
cargo tauri build
```

## Contract

The sidecar must:

- listen on `http://127.0.0.1:8765` (control plane from `proto/contracts.md`),
- create `%APPDATA%\ai.jarvis.shell\.token` on first run (the shell reads it
  to authenticate; see `shell/src/ipc.rs::token_path`),
- honour the `JARVIS_MANIFEST` env var for the pinned-model manifest.

Anything that breaks this contract breaks the installer — keep
`packaging/sidecar.spec`'s core-only profile in sync with
`sidecar/jarvis/ipc/server.py`'s real import closure.
