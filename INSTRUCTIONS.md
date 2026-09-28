# JARVIS — Full Source Package — Setup & Instructions

This package is the complete JARVIS source tree: the Python sidecar
(brain, agent, tools, memory, voice/vision/shell interfaces), the
Tauri v2 Rust shell, the packaging/CI pipeline, evals, docs, and the
phase plans (1–30). Everything below was verified on 2026-09-27:
**1,268 unit tests pass, 7 skipped.**

## What's in the box

```
jarvis/
  sidecar/        The brain. Python 3.10+ (tested on 3.12).
                  jarvis/tools/builtin/  — 60+ tool packs, 329 static tools
                  jarvis/tools/dynamic/  — 440,346 computed tools
                    (unit conversions, colors, timezones, web actions…)
                  440,675 total addressable, 0 duplicate names
                  jarvis/agent/          — ReAct loop, policy engine,
                                           autonomy (allow/ask/deny), kill switch
                  jarvis/memory/         — episodic + semantic memory (SQLite)
                  tests/unit/            — the full test suite
  shell/          Tauri v2 Rust desktop shell (tray, hotkeys, mic/cam,
                  chat window). Needs Rust + Node to build.
  packaging/      PyInstaller spec (sidecar) + Tauri bundling + the
                  GitHub Actions workflow that builds the Windows
                  installer (.msi + setup.exe) on version tags.
  plans/          Phase plans 1–39. All built and tested.
  docs/           Architecture decision log (DECISIONS.md) and more.
  evals/          Regression + failure-set harness.
  models/         Model manifest (pinned, hash-verified, consent-gated).
  proto/          Shell ↔ sidecar IPC contract.
  tools_config.yaml   Per-tool configuration (features on/off, keys).
```

Not included (regenerated at runtime): `data/` (SQLite stores),
`build/`, `dist/`, `output/` (generated media/sites), `.git/`.

## Requirements

- Python 3.10+ with `pip`
- `pip install pyyaml fastapi uvicorn websockets pillow`
- Optional, never required: `torch` + `sentence-transformers`
  (semantic brain), `ffmpeg` (video compose), model files (voice/vision)
- Rust + Node.js only if you want to build the desktop shell

## Run the tests (do this first)

```bash
cd sidecar
python3 -m pytest tests/unit -q
# expected: 1268 passed, 7 skipped
```

## Run the brain

```bash
cd sidecar
python3 -c "
import sys; sys.path.insert(0, '.')
from jarvis.tools.base import Registry
from jarvis.tools.dynamic import build_dynamic_registry
import importlib, pkgutil
import jarvis.tools.builtin as b
reg = Registry()
for mod in pkgutil.iter_modules(b.__path__):
    m = importlib.import_module(f'jarvis.tools.builtin.{mod.name}')
    if hasattr(m, 'register'):
        m.register(reg)
print('static tools:', len(reg.tools))
dyn = build_dynamic_registry(None, None)
print('total addressable:', len(reg.tools) + dyn.count_addressable())
"
```

Tool calls go through the registry (`reg.tools["brain.think"].handler(args)`),
gated by `jarvis/agent/policy.py` (per-tool allow/ask/deny + kill switch).
Wire each pack's `RISK_TABLE_ADDITIONS` into the policy table — see the
comment at the top of each pack's `RISK_TABLE_ADDITIONS` block.

## Configure

Copy and edit `tools_config.yaml`: feature flags, API keys, paths,
autonomy grants. Nothing phones home by default; every network/network-model
feature is opt-in and fails honestly when unavailable.

## The 39 phases (what was built, in order)

1–6: voice loop, agent, policy engine, core tools, memory v1, vision/RAG/mail/calendar/browser, trainer
7–15: plugin SDK, GUI control, Playwright browser, winget PC mgmt, Telegram, Gmail OAuth, studio (podcast/reels), tool forging, agent swarms, background workers, device API, phone conversations, inbox triage, converters/encoders, AGI autonomy (approval queue, budget, kill switch), voice loop, eyes (screen/camera/OCR), self-learning memory, Windows shell, teach-by-showing macros, sleep-and-dream cycle, sandboxed self-upgrades, promise tracking, structured deliberation, offline knowledge base (516 docs), episodic memory, deep researcher, data analyst, software engineer, relationship keeper
16–17: JEV/Laya System-1 fast layer (optional) + System 1/2 fusion
18: (absorbed into 17's fusion work)
19: metacognitive brain — steelman, premortem, assumptions, second-order, real Brier-score calibration
20: closed-loop cognition — decision cycles with outcomes
21: semantic brain — optional embeddings, cosine-weighted deliberation
22: accountable dreamer — `brain.review` + 2am briefing hook
23: contradiction miner — `brain.contradictions`
24: verified execution — per-step `expect` clauses
25: memory consolidation — `memory.consolidate`
26: trace auditor — `brain.audit`
27: approval digest — `autopilot.digest`
28: correction learning — `brain.correct`
29: session handoff — `brain.handoff` / `brain.resume`
30: integrity loop (`brain.integrity`) — one call running review +
    contradictions + audit sample + calibration + digest into a single
    state-of-the-brain report; wired into the 2am sleep cycle
31: scheduled autonomy — `autopilot.schedule` / `tick` (tick files approval
    proposals only, never executes; host calls tick)
32: milestone plans — optional milestones on `brain.plan`, completable only
    when bound steps are done; `brain.milestone`, progress in `plan_status`
33: structured debate — `brain.debate` (2–4 sides, one mechanical brain,
    multiple primed passes, judged)
34: topic consolidation — `memory.consolidate {by_topic}` groups episodes
    via semantic clustering, honest fallback when the backend is off
35: explainability — `agent.why` reads the audit log ("why did you do that?")
36: email triage replies — `inbox.triage_replies` drafts replies as approval
    proposals; nothing is ever sent directly
37: cited research — `research.cite` renders briefings as numbered-citation
    reports; unverified claims are never cited
38: coding workspace — `build.note` / `build.resume` (session log +
    pick-up-where-you-stopped)
39: pattern noticing — `proactive.patterns` mines the audit log for repeats
    and queues suggestions (frequency, not understanding)

Also fixed: the PyInstaller spec now builds from `sidecar/server_main.py`
(a top-level shim with absolute imports only) instead of
`jarvis/ipc/server.py` directly — a frozen entry script runs as
`__main__` with no parent package, so server.py's relative imports
crashed the Windows exe on launch.

## Build the Windows installer

```bash
git tag v0.3.0 && git push origin v0.3.0
# GitHub Actions builds JARVIS_<ver>_x64-setup.exe + .msi on the tag.
```

No code-signing certificate: Windows SmartScreen will show
"Unknown publisher" → Run anyway. First-run smoke tests still open:
app launch, tray, hotkeys, sidecar startup, voice I/O, screen capture,
Windows shell actions — all need a real Windows PC.

## Honest limits (read before demoing)

- Reasoning is mechanical and partly heuristic; embeddings measure
  similarity, not truth. Nothing here invents facts — the knowledge
  base omits rather than hallucinates.
- Phone calls need Twilio credentials; background workers need the
  autonomy opt-in; voice/vision/shell need the Windows PC + models.
- JEV/Laya is optional and disabled by default — every System-1 tool
  returns an honest `{"ok": False}` when it's missing, never fakes it.
- The real `sentence-transformers` backend was not verified on the
  build machine; the semantic tools ship with an honest keyword
  fallback and say so.
- Autonomy acts only inside granted permissions; everything else
  queues for batch approval; the kill switch is always available.
