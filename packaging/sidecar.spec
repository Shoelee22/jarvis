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
    # Entry MUST be the top-level shim (server_main.py), never
    # jarvis/ipc/server.py directly: the frozen entry script runs as
    # __main__ with no parent package, so server.py's relative imports
    # (`from ..config import ...`) die with "attempted relative import
    # with no known parent package". The shim uses absolute imports only.
    [os.path.join(SIDECAR_DIR, "server_main.py")],
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
        "jarvis.llm_server",
        "jarvis.ipc.server",
        "jarvis.agent.core",
        "jarvis.agent.audit",
        "jarvis.agent.policy",
        "jarvis.agent.llama_backend",
        "jarvis.tools.base",
        "jarvis.tools.builtin",
        # All builtin tool packs: listed explicitly because PyInstaller's
        # static analysis of the long `from . import (...)` in
        # jarvis/tools/builtin/__init__.py is not reliable on all platforms
        # (v0.3.1 Windows bundle silently missed analyst_pack, which crashes
        # startup with a misleading "circular import" ImportError).
        "jarvis.tools.builtin.agent_pack",
        "jarvis.tools.builtin.analyst_pack",
        "jarvis.tools.builtin.autonomy_pack",
        "jarvis.tools.builtin.autopilot_pack",
        "jarvis.tools.builtin.brain_pack",
        "jarvis.tools.builtin.browser_pack",
        "jarvis.tools.builtin.builder_pack",
        "jarvis.tools.builtin.cognition_pack",
        "jarvis.tools.builtin.comms",
        "jarvis.tools.builtin.creator",
        "jarvis.tools.builtin.crm_pack",
        "jarvis.tools.builtin.data_pack",
        "jarvis.tools.builtin.delegate_tool",
        "jarvis.tools.builtin.dev_pack",
        "jarvis.tools.builtin.devops_pack",
        "jarvis.tools.builtin.doctor_pack",
        "jarvis.tools.builtin.edu_pack",
        "jarvis.tools.builtin.episodic_pack",
        "jarvis.tools.builtin.finance_pack",
        "jarvis.tools.builtin.forge_pack",
        "jarvis.tools.builtin.fs_tools",
        "jarvis.tools.builtin.gmail_pack",
        "jarvis.tools.builtin.gui_pack",
        "jarvis.tools.builtin.health_pack",
        "jarvis.tools.builtin.home_pack",
        "jarvis.tools.builtin.inbox_pack",
        "jarvis.tools.builtin.knowledge_pack",
        "jarvis.tools.builtin.loops_pack",
        "jarvis.tools.builtin.media_pack",
        "jarvis.tools.builtin.meetings_pack",
        "jarvis.tools.builtin.metacog_pack",
        "jarvis.tools.builtin.mind_pack",
        "jarvis.tools.builtin.misc",
        "jarvis.tools.builtin.net_tools",
        "jarvis.tools.builtin.news_pack",
        "jarvis.tools.builtin.pc_pack",
        "jarvis.tools.builtin.pdf_pack",
        "jarvis.tools.builtin.permissions_pack",
        "jarvis.tools.builtin.persona_pack",
        "jarvis.tools.builtin.phone_pack",
        "jarvis.tools.builtin.phone_stream",
        "jarvis.tools.builtin.proactive_pack",
        "jarvis.tools.builtin.productivity",
        "jarvis.tools.builtin.projects_pack",
        "jarvis.tools.builtin.research_pack",
        "jarvis.tools.builtin.schedules_pack",
        "jarvis.tools.builtin.security_pack",
        "jarvis.tools.builtin.self_pack",
        "jarvis.tools.builtin.semantics_pack",
        "jarvis.tools.builtin.shell_pack",
        "jarvis.tools.builtin.shell_tools",
        "jarvis.tools.builtin.sleep_pack",
        "jarvis.tools.builtin.studio_pack",
        "jarvis.tools.builtin.swarm_pack",
        "jarvis.tools.builtin.system1_pack",
        "jarvis.tools.builtin.system_pack",
        "jarvis.tools.builtin.teach_pack",
        "jarvis.tools.builtin.telegram_pack",
        "jarvis.tools.builtin.translate_pack",
        "jarvis.tools.builtin.travel_pack",
        "jarvis.tools.builtin.vision_pack",
        "jarvis.tools.builtin.voice_pack",
        "jarvis.tools.builtin.weather_pack",
        "jarvis.tools.builtin.web_pack",
        "jarvis.tools.builtin.workers_pack",
        "jarvis.memory.store",
        "jarvis.memory.vectors",
        "jarvis.security.egress",
        # tools_config.yaml reader (guarded import in creator.py, but core feature)
        "yaml",
        # uvicorn selects its HTTP/WS protocol and event loop dynamically
        "uvicorn",
        "uvicorn.config",
        "uvicorn.server",
        "uvicorn.logging",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "uvicorn.lifespan.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.auto",
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
