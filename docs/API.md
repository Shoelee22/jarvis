# Jarvis API Mode

**What it is:** Phase 9 turns the JARVIS sidecar into an authenticated HTTP
API. Any device or app on the network (his phone, a dashboard, an automation)
can hit the assistant's brain over plain HTTPS-free HTTP + a bearer token —
the same FastAPI server that already serves the desktop control plane.

**Module:** `sidecar/jarvis/ipc/api_mode.py` — new routes mounted on the
*existing* `app = FastAPI(title="jarvis-sidecar")` from `ipc/server.py`.
It does not touch any existing route; it only adds `/api/v1/*`.

**fastapi is optional.** It is NOT installed on the dev VM. `api_mode.py`
never imports fastapi at module import time (all fastapi imports are lazy
inside `mount_api()`), so `import api_mode` always works and the unit tests
run fully offline. The live HTTP path is tested with FastAPI's `TestClient`
and skipped when fastapi/httpx are absent.

---

## How the parent enables it (integration patch for `ipc/server.py`)

Paste this into `build_app()` — right after the existing `bind_agent(agent)`
call and before `return app`:

```python
from .api_mode import mount_api, bind_host

mount_api(app, agent=agent)
```

That single line registers all three routes. The `agent` you already build in
`build_app()` is the same object `agent.run(...)` calls into — no new agent
wiring needed. Late binding also works: call `mount_api(app)` first and
`set_agent(agent)` later (same pattern as `delegate_tool.bind_agent()`).

Then, in `main()`, swap the fixed bind host for the config-aware one:

```python
from .api_mode import bind_host

uvicorn.run("jarvis.ipc.server:build_app", factory=True,
            host=bind_host(), port=args.port, reload=args.reload)
```

`bind_host()` reads `api.lan_enabled` from
`~/workspace/jarvis/dynamic/tools_config.phase9.yaml` (default FALSE → binds
`127.0.0.1`; `true` → binds `0.0.0.0`). Any config problem — missing file,
missing section, bad YAML — defensively falls back to `127.0.0.1`.

To enable LAN mode, add to the Phase 9 config:

```yaml
api:
  lan_enabled: true   # default false — see SECURITY below
```

---

## Routes

| Method | Path              | Auth | What it does                                    |
| ------ | ----------------- | ---- | ----------------------------------------------- |
| GET    | `/api/v1/health`  | no   | `{"ok": true, "version": "1.0.0"}`              |
| GET    | `/api/v1/tools`   | yes  | registry `spec_list()` — name/desc/risk per tool |
| POST   | `/api/v1/ask`     | yes  | run the agent on `{"text": ..., "confirmed_tools": [...]}` |

`POST /api/v1/ask` response:

```json
{
  "reply": "At your service, sir.",
  "done": true,
  "tool_trace": [
    {"tool": "time.now", "args": {}, "result": "2026-09-27T10:30:00+05:30"}
  ],
  "took_ms": 42
}
```

- `confirmed_tools` — optional list of tool names pre-confirmed by the
  caller (e.g. the phone app already showed a confirm sheet). High-risk
  tools still go through the normal confirmation flow: the response comes
  back with `done: false` and a reply asking for confirmation.
- Each `tool_trace[].result` is truncated to 500 chars.
- `text` over 4000 chars is rejected with `400`.
- Honest errors, never stack traces: no agent bound yet → `503`; bad token
  → `401`; token machinery unavailable → `503 "auth unavailable"`.

Auth is a bearer token in the `Authorization` header, compared with
`hmac.compare_digest` (constant-time). The default token getter imports
`ensure_token()` from `ipc/server.py` — the same token the desktop control
plane already uses.

---

## curl examples

Base URL when LAN mode is off (the default): `http://127.0.0.1:8765`.
Get the token from the server's token file on the machine running the sidecar.

Health (no auth needed):

```bash
curl http://127.0.0.1:8765/api/v1/health
# {"ok":true,"version":"1.0.0"}
```

List tools (authenticated):

```bash
TOKEN=$(cat ~/.local/share/jarvis/token)   # wherever TOKEN_FILE points
curl -H "Authorization: Bearer $TOKEN" \
     http://127.0.0.1:8765/api/v1/tools | head -c 600
```

Ask the assistant (authenticated):

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"text": "what is on my calendar today?"}' \
     http://127.0.0.1:8765/api/v1/ask
```

With a pre-confirmed low-risk tool:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"text": "what time is it", "confirmed_tools": ["time.now"]}' \
     http://127.0.0.1:8765/api/v1/ask
```

A phone on the same Wi-Fi (after enabling LAN mode, next section):

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"text": "remind me to call the gym at 6pm"}' \
     http://192.168.1.20:8765/api/v1/ask
```

---

## Security

Read this before enabling LAN mode.

1. **LAN mode exposes your assistant to the network.** `api.lan_enabled: true`
   binds `0.0.0.0` — every device on the LAN (and, if the machine is exposed,
   the internet) can reach the API. Only enable it on networks you trust
   (home Wi-Fi, not café Wi-Fi). Default is loopback-only (`127.0.0.1`).
2. **The bearer token is the ONLY authentication.** Whoever holds it can run
   any tool the agent can run — including high-risk ones if they confirm
   them. Keep the token file (`chmod 600`, same as the desktop control plane
   uses) and never paste it into chat logs, screenshots, or repos.
3. **No TLS on the wire.** The sidecar serves plain HTTP. On a trusted LAN
   this is acceptable; never put this API on the public internet without a
   reverse proxy (Caddy/nginx) terminating TLS in front of it.
4. **Failed-by-default.** If the token getter can't be imported, every
   authed route answers `503 "auth unavailable"` — it fails closed, never
   open. If `api.lan_enabled` is missing or malformed, the bind host falls
   back to `127.0.0.1`.
5. **Size guard, no streaming secrets.** `/api/v1/ask` rejects bodies over
   4000 chars with `400`; tool output in the trace is truncated at 500
   chars. Errors are honest `4xx/5xx` JSON — never tracebacks, never leaked
   internals.

---

## Gaps / follow-ups

- The Phase 9 config file `dynamic/tools_config.phase9.yaml` does not exist
  yet (only `tools_config.phase8.yaml` ships) — `bind_host()` treats that as
  `lan_enabled: false`. The parent should create the file when other Phase 9
  pieces land, and can add `api.lan_enabled: true` there at that point.
- Token distribution to his phone is manual (copy the token string once).
  A proper pairing flow (QR code on the desktop UI) is out of scope for this
  pass.
- `uvicorn.run` with a fixed `127.0.0.1` in `server.py main()` — the
  integration patch above replaces it with `bind_host()`; until the parent
  applies it, the server stays loopback-only.
