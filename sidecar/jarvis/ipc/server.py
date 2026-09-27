"""Sidecar HTTP control plane + WS audio. Localhost only, token-authed."""
from __future__ import annotations
import secrets
from pathlib import Path

from ..config import Settings, TOKEN_FILE, DATA_DIR

settings = Settings()


def ensure_token() -> str:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    token = secrets.token_hex(32)
    TOKEN_FILE.write_text(token)
    TOKEN_FILE.chmod(0o600)
    return token


def build_app():
    from fastapi import FastAPI, Depends, HTTPException, Header  # type: ignore
    from ..agent.core import Agent, FakeLLM
    from ..agent.audit import AuditLog
    from ..tools.builtin import build_registry
    from ..tools.builtin.delegate_tool import bind_agent
    from ..memory.store import MemoryStore

    token = ensure_token()
    app = FastAPI(title="jarvis-sidecar")
    audit = AuditLog(settings.data_dir / "jarvis.db")
    registry = build_registry(settings.data_dir)
    memory = MemoryStore(settings.data_dir / "brain.db")
    agent = Agent(FakeLLM(), registry, audit, memory=memory)
    bind_agent(agent)  # enables the tasks.delegate tool

    # --- Phase 9: swarm/workers/phone bind to the live agent; public API mode ---
    from ..tools.builtin import swarm_pack, workers_pack, phone_pack
    swarm_pack.bind_agent(agent)
    workers_pack.bind_agent(agent)
    phone_pack.bind_agent(agent)
    # --- Phase 11: autopilot/proactive/autonomy bind to the live agent ---
    from ..tools.builtin import autopilot_pack, proactive_pack, autonomy_pack
    autopilot_pack.bind_agent(agent)
    proactive_pack.bind_agent(agent)
    autonomy_pack.bind_agent(agent)
    # --- Phase 12: teach pack binds to the live agent (macro.play) ---
    from ..tools.builtin import teach_pack
    teach_pack.bind_agent(agent)
    # --- Phase 13: capstone packs bind to the live agent ---
    from ..tools.builtin import sleep_pack, self_pack, loops_pack, persona_pack
    sleep_pack.bind_agent(agent)
    self_pack.bind_agent(agent)
    loops_pack.bind_agent(agent)
    persona_pack.bind_agent(agent)
    # --- Phase 14: brain pack binds to the live agent (run_step/reflect) ---
    from ..tools.builtin import brain_pack
    brain_pack.bind_agent(agent)
    # --- Phase 15: research/builder/crm packs bind to the live agent ---
    from ..tools.builtin import research_pack, builder_pack, crm_pack
    research_pack.bind_agent(agent)
    builder_pack.bind_agent(agent)
    crm_pack.bind_agent(agent)
    from .api_mode import mount_api, bind_host
    mount_api(app, agent=agent)

    # Background workers tick every 60s (workers.spawn schedules fire here).
    import asyncio

    @app.on_event("startup")
    async def _workers_tick_loop():
        async def _loop():
            while True:
                await asyncio.sleep(60)
                try:
                    registry.call("workers.tick", {}, actor="scheduler")
                except Exception:
                    pass
                # --- Phase 13: nightly sleep cycle (~02:00 local; idempotent per night) ---
                try:
                    from datetime import datetime as _dt
                    if _dt.now().hour == 2:
                        registry.call("sleep.cycle", {}, actor="scheduler")
                except Exception:
                    pass
        asyncio.create_task(_loop())

    def auth(authorization: str = Header("")):
        if authorization != f"Bearer {token}":
            raise HTTPException(401, "bad token")

    @app.get("/v1/health")
    def health():
        return {"status": "ok", "model_loaded": False, "note": "FakeLLM (dev)"}

    @app.get("/v1/tools", dependencies=[Depends(auth)])
    def tools():
        return registry.spec_list()

    @app.post("/v1/chat", dependencies=[Depends(auth)])
    def chat(body: dict):
        return agent.run(body.get("text", ""))

    @app.post("/v1/tools/call", dependencies=[Depends(auth)])
    def tool_call(body: dict):
        return registry.call(body["name"], body.get("args", {}), audit=audit,
                             confirmed=body.get("confirmed", False))

    @app.get("/v1/memory/search", dependencies=[Depends(auth)])
    def mem_search(q: str):
        return memory.search(q)

    @app.post("/v1/memory/forget", dependencies=[Depends(auth)])
    def mem_forget(body: dict):
        return {"deleted": memory.forget(body.get("query", ""))}

    @app.post("/v1/settings/autonomy", dependencies=[Depends(auth)])
    def set_autonomy(body: dict):
        level = body.get("level", "assisted")
        assert level in ("manual", "assisted", "autonomous")
        settings.autonomy = level
        return {"ok": True, "autonomy": level}

    return app


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    try:
        import uvicorn  # type: ignore
    except ImportError:
        print("uvicorn/fastapi not installed — install sidecar[server] to run the server.")
        print("Core logic is still fully testable: make sidecar-test")
        raise SystemExit(1)
    from .api_mode import bind_host
    uvicorn.run("jarvis.ipc.server:build_app", factory=True, host=bind_host(),
                port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
