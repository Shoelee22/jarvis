# JARVIS — build repo

Ultimate local-brain voice assistant. See `../jarvis-ultimate/` for PRD / REQUIREMENTS / TRD /
master prompt. This repo is the implementation, built phase by phase.

```
jarvis/
  docs/        DECISIONS.md (architecture decision log)
  proto/       contracts.md — shell ↔ sidecar IPC contract
  models/      manifest.json — pinned models, hashes, licenses
  evals/       regression.jsonl, failure_set.jsonl, harness.py
  sidecar/     Python brain (voice, agent, tools, memory, vision, trainer)
  shell/       Tauri v2 Rust shell (tray, hotkey, mic/cam, chat window)
  .github/     CI: lint + tests + per-OS build matrix
```

## Quick start

```bash
make dev        # shell + sidecar with hot reload (needs models first)
make models     # download pinned models (hash-verified, with consent)
make test       # unit + eval harness
make installer  # per-OS installer (CI does this on tags)
```

## Phases

- **Phase 0** — scaffold, contracts, CI, eval harness ✅ (you are here)
- **Phase 1** — MVP: voice loop, agent, policy engine, core tools, memory v1
- **Phase 2** — v1: vision, RAG, mail/calendar/browser tools, trainer + self-tuning
- **Phase 3** — polish: smart home, routines UI, Hinglish pack, beta hardening

Hardware notes: Recommended = 16GB RAM (uncensored Qwen3-8B-Q4_K_M + Qwen2.5-VL-7B).
GPU optional. Everything runs offline after first-run model download.
