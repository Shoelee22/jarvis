# Shell ↔ Sidecar IPC Contract (v1)

Transport: HTTP/JSON control plane + WebSocket for audio streams. Localhost only.
Auth: bearer token from `~/.jarvis/.token` (0600). All endpoints below are sidecar-side.

## Control plane

| Method & path | Body → Response |
|---|---|
| `GET /v1/health` | → `{status, sidecar_version, model_loaded, uptime_s}` |
| `POST /v1/listen/start` | `{mode: wakeword\|push_to_talk\|continuous}` → `{session_id}` |
| `POST /v1/listen/stop` | `{session_id}` → `{ok}` |
| `POST /v1/chat` | `{text, session_id?}` → `{reply_text, tool_calls:[...], audio_url?}` |
| `POST /v1/vision/describe` | `{source: camera\|screen, save: bool}` → `{description, saved_path?}` |
| `POST /v1/tools/call` | `{name, args}` → `{ok, result|error, risk, confirmed}` |
| `GET /v1/tools` | → `[{name, description, risk, needs_network}]` |
| `GET /v1/memory/search?q=` | → `[{id, type, text, confidence}]` |
| `POST /v1/memory/forget` | `{query}` → `{deleted: n}` |
| `POST /v1/training/jobs` | `{kind: manual\|selftune, dataset_ref}` → `{job_id}` |
| `GET /v1/training/jobs/{id}` | → `{status, progress, eval_delta?}` |
| `POST /v1/models/rollback` | `{}` → `{active_checkpoint}` |
| `POST /v1/settings/autonomy` | `{level: manual\|assisted\|autonomous}` → `{ok}` |

## Audio WebSocket — `WS /v1/audio?session_id=`

Binary PCM16 16kHz frames client→server. JSON events both ways:

- C→S: `{event:"barge_in"}` (user started speaking during TTS)
- S→C: `{event:"wake"}`, `{event:"partial", text}`, `{event:"final", text}`,
  `{event:"tts_chunk", seq, bytes_b64}`, `{event:"tts_done"}`,
  `{event:"tool_start", name}`, `{event:"tool_done", name, ok}`

## Tool call shape (MCP-compatible)

```json
{"name": "fs.read", "args": {"path": "/home/user/notes.txt"},
 "meta": {"risk": "low", "needs_network": false, "actor": "agent"}}
```

Result: `{"ok": true, "result": "...", "truncated": false}` or
`{"ok": false, "error": "...", "needs_confirmation": true}` for high-risk calls
awaiting user confirmation (UI re-POSTs with `"confirmed": true`).
