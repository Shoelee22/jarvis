# DECISIONS.md — Architecture Decision Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-09-26 | Tauri v2 shell + Python sidecar over localhost HTTP/WS (not gRPC) | HTTP+WebSocket is enough for our streaming needs, far easier to debug and test without protoc; gRPC kept as future option |
| 2026-09-26 | SQLCipher optional — fallback to plain SQLite with a loud warning | Keeps tests/dev runnable everywhere; production installer enables SQLCipher |
| 2026-09-26 | LanceDB optional — fallback to in-process brute-force vector search | Zero-dependency dev/test; LanceDB used when installed |
| 2026-09-26 | ML backends (whisper, piper, llama.cpp) behind lazy interfaces + fakes | Core logic (agent, policy, memory, self-tuning) is fully testable without models or GPU |
| 2026-09-26 | Uncensored base model; safety enforced by policy engine, not refusals | Per requirements FR-8.7; policy rules are user config, never trainable |
| 2026-09-26 | Tool protocol: JSON-over-HTTP, MCP-compatible shapes | MCP compatibility without the MCP SDK dependency; can adopt SDK later |
