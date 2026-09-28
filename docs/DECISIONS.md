# DECISIONS.md — Architecture Decision Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-09-26 | Tauri v2 shell + Python sidecar over localhost HTTP/WS (not gRPC) | HTTP+WebSocket is enough for our streaming needs, far easier to debug and test without protoc; gRPC kept as future option |
| 2026-09-26 | SQLCipher optional — fallback to plain SQLite with a loud warning | Keeps tests/dev runnable everywhere; production installer enables SQLCipher |
| 2026-09-26 | LanceDB optional — fallback to in-process brute-force vector search | Zero-dependency dev/test; LanceDB used when installed |
| 2026-09-26 | ML backends (whisper, piper, llama.cpp) behind lazy interfaces + fakes | Core logic (agent, policy, memory, self-tuning) is fully testable without models or GPU |
| 2026-09-26 | Uncensored base model; safety enforced by policy engine, not refusals | Per requirements FR-8.7; policy rules are user config, never trainable |
| 2026-09-26 | Tool protocol: JSON-over-HTTP, MCP-compatible shapes | MCP compatibility without the MCP SDK dependency; can adopt SDK later |
| 2026-09-27 | Phase 18: extend the existing dynamic automation provider, never build a second automation engine | schedules.create_routine is a human-facing scheduling layer (natural language + timezone + quiet hours) that writes into the same routine store as automation.create |
| 2026-09-27 | Phase 18: promises from projects/meetings link into existing open-loop tracking (loops.track) | One promise system; project/meeting tools carry a `promise` field that routes to the same loop registry instead of inventing a parallel one |
| 2026-09-27 | Phase 18: security.audit is a local verifier, never returns secret values | Audit reports present/absent/file-mode only; a leaked value in a report would be worse than the vulnerability |
| 2026-09-27 | Phase 18: natural-language cron via stdlib (no croniter dependency) | The parser handles the common shapes (every N min/hr, daily/weekly/monthly, weekdays) with a documented honest gap: complex expressions fall back to the exact cron string |
