# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the JARVIS sidecar (Windows installer bundle).

Build from the REPO ROOT (Windows):

    pyinstaller packaging/sidecar.spec

Produces: dist/jarvis-sidecar.exe

CI then renames it to the Tauri external-binary convention and drops it in
shell/binaries/:

    jarvis-sidecar-x86_64-pc-windows-msvc.exe

Bundle profile: CORE ONLY. The frozen exe contains the stdlib, FastAPI/uvicorn
(the HTTP control plane), PyYAML (tools_config.yaml) and the jarvis package
minus the heavy optional backends. Everything heavy — faster-whisper, Piper,
llama-cpp, torch, lancedb — is imported lazily inside functions and stays OUT
of the bundle (see `excludes` below). ML weights are NEVER bundled; they are
downloaded on first run with the user's consent (~10GB into the per-user data
dir).

Verified import closure (2026-09-27): no module in the
jarvis.ipc.server runtime path imports any third-party package at module
top level. The only runtime third-party requirements are fastapi, uvicorn
and yaml. Hidden imports below cover the function-level imports PyInstaller's
static analysis can miss (uvicorn's dynamic protocol/loop selection).
"""

import os

REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SIDECAR_DIR = os.path.join(REPO_ROOT, "sidecar")

block_cipher = None

a = Analysis(
    [os.path.join(SIDECAR_DIR, "jarvis", "ipc", "server.py")],
    pathex=[SIDECAR_DIR],
    binaries=[],
    # Phase 14: offline knowledge datasets (elements, countries, constants,
    # timeline, science facts) — knowledge.* tools read these at runtime.
    datas=[
        (os.path.join(SIDECAR_DIR, "jarvis", "tools", "builtin", "data"),
         os.path.join("jarvis", "tools", "builtin", "data")),
    ],
    hiddenimports=[
        # jarvis package: the runtime path of jarvis.ipc.server
        "jarvis",
        "jarvis.config",
        "jarvis.models",
        "jarvis.ipc.server",
        "jarvis.agent.core",
        "jarvis.agent.audit",
        "jarvis.agent.policy",
        "jarvis.tools.base",
        "jarvis.tools.builtin",
        "jarvis.tools.builtin.fs_tools",
        "jarvis.tools.builtin.shell_tools",
        "jarvis.tools.builtin.productivity",
        "jarvis.tools.builtin.misc",
        "jarvis.tools.builtin.net_tools",
        "jarvis.tools.builtin.creator",
        "jarvis.memory.store",
        "jarvis.memory.vectors",
        "jarvis.security.egress",
        # tools_config.yaml reader (guarded import in creator.py, but core feature)
        "yaml",
        # uvicorn selects its HTTP/WS protocol and event loop dynamically
        "uvicorn",
        "uvicorn.config",
        "uvicorn.server",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.loops.uvloop",
        # fastapi/starlette are found statically, listed for safety
        "fastapi",
        "starlette",
        "websockets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy optional backends: imported lazily inside functions, never
        # needed by the core bundle. Excluding keeps the installer small.
        "torch",
        "numpy",
        "cv2",
        "faster_whisper",
        "onnxruntime",
        "piper",
        "piper_tts",
        "lancedb",
        "pyarrow",
        "sqlcipher3",
        "llama_cpp",
        "unsloth",
        "axolotl",
        # stdlib modules the sidecar never uses
        "tkinter",
        "unittest",
        "pydoc",
        "doctest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="jarvis-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # No console window: the shell supervises the sidecar silently.
    # (uvicorn logs to stderr, which is discarded in windowed mode.)
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
