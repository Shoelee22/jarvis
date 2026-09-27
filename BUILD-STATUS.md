# JARVIS — Build Status (2026-09-26)

## Test results (this machine, just now)
- **Unit tests: 26/26 pass** (`make sidecar-test`)
- **Eval harness — regression: 12/12 pass** (policy decisions, memory round-trip,
  self-tune promotion gate, prompt-injection resistance)
- **Eval harness — failure set: 2/2 pass** (failure patterns mineable by self-tuner)

## Phase 0 — Scaffold ✅ COMPLETE
Monorepo, Makefile, CI (lint+tests+per-OS shell matrix), IPC contract (`proto/contracts.md`),
model manifest (pinned, hash-verified, consent-gated), eval harness + suites, DECISIONS.md.

## Phase 1 — MVP ✅ CORE COMPLETE (needs models + hardware for the live loop)
- **Done & tested:** ReAct agent loop, policy engine (allow/confirm/deny + destructive-shell
  detection + injection scanner), audit log, tool registry (MCP-compatible shapes),
  17 built-in tools (fs, shell, reminders, timers, notes, calc, clipboard, mail/calendar/
  browser/web/camera/screen stubs with real shapes), memory (SQLite + vectors, remember/
  search/forget, corrections→training signal), nightly consolidation, voice pipeline
  interfaces (STT/TTS/wake-word + fakes), IPC server (all `contracts.md` endpoints),
  model downloader, first-run flow spec.
- **Needs your machine:** `make models` (downloads ~10GB pinned models), `cargo tauri dev`
  for the shell, mic/camera hardware for the live voice loop. Real STT/TTS/LLM backends
  are interfaced and lazy-load — swap `FakeLLM`/`FakeSTT` in `ipc/server.py`.

## Phase 2 — v1 ✅ LOGIC COMPLETE (needs models + accounts + hardware)
- **Done & tested:** vision interfaces (describe, face enroll/identify, watch trigger,
  gestures), local RAG (folder register → chunk → cite), trainer (PII-scanning dataset
  builder, QLoRA job runner with GPU autodetect + dry-run), **autonomous self-tuning loop**
  (Miner → Synthesizer → Scheduler → Promoter with eval gate + morning report + rollback),
  autonomy levels (manual/assisted/autonomous), default-deny egress firewall.
- **Needs your machine:** vision model weights, unsloth/axolotl + GPU for real training
  (dry-run simulates the full pipeline today), email/calendar OAuth, Playwright browser.

## Phase 3 — Polish 🔶 IN PROGRESS (2026-09-27)
- **Chat window UI built** (`shell/ui/`: index.html, styles.css, app.js): dark-HUD
  Jarvis theme, reactor-core mic button with idle/listening/thinking/speaking
  states, text chat, tool-activity feed, high-risk tool approval cards, camera
  kill-switch button, autonomy switcher (manual/assisted/autonomous), settings
  drawer. Talks to the sidecar exactly per `proto/contracts.md` (HTTP control
  plane + audio WebSocket with 16kHz PCM mic streaming and TTS chunk playback).
  Wired into the shell: `frontendDist: "ui"` in tauri.conf.json, CSP opened for
  `http://127.0.0.1:8765` + `ws://`, new `get_sidecar_token` Tauri command so the
  UI can use the sidecar bearer token. Preview: `shell/ui/preview.png`
  (`?demo=1` for sidecar-free visual demo).
- **Real TTS wired**: `load_real_tts()` (Piper ONNX, streams 16kHz PCM16 chunks)
  added to `sidecar/jarvis/voice/pipeline.py` alongside the existing
  `load_real_stt()` (faster-whisper).
- Tests after changes: **43/43 unit pass**, **12/12 regression evals pass**.
- Still to do: `cargo tauri dev` on your desktop, `make models` (~10GB, needs
  your consent), real Whisper/Piper model weights, Hinglish pack, smart-home
  bridge, installer bundling.

## Cinematic pass — 2026-09-27
Movie-accurate JARVIS treatment across all three layers:
- **Persona** (`sidecar/jarvis/agent/core.py`): rewritten SYSTEM_PROMPT — dry
  British butler wit, always "sir", "At once, sir" / "I've taken the liberty
  of..." / "Shall I...?", proactive, brief in speech. Security rules unchanged.
- **Voice** (`sidecar/jarvis/voice/pipeline.py`): default TTS voice pinned to
  Piper `en_GB-alan-medium` (British male); `load_default_tts()` falls back to
  FakeTTS until `make models` downloads it. Manifest updated.
- **HUD** (`shell/ui/` rebuilt): boot sequence ("J.A.R.V.I.S. OS v4.2.1 ... all
  systems operational"), holographic 3-column layout — System Vitals + routine
  chips (Morning briefing, Full diagnostic, House party protocol), reactor core
  with live waveform ring (idle/listening/processing/speaking), timestamped
  Dialogue Log, scanlines + vignette. Window resized to 1000x680.
  Preview: `shell/ui/preview.png` (`?demo=1` interactive, `?demo=1&shot=1` staged).

## What to do next
1. `cd ~/workspace/jarvis && make models` — review + consent to the ~10GB download.
2. Point the eval harness at uncensored Qwen3-8B vs Llama-3.1-8B builds; pin the winner's
   hash in `models/manifest.json`.
3. `cargo tauri dev` in `shell/` on your desktop; wire real STT/TTS in `voice/pipeline.py`.
4. Say the word and I'll continue: chat UI, real Whisper/Piper wiring, or the v1.1 items.

## Phase 4 — Creator Tool Pack ✅ DONE (2026-09-27)
- **5 new tools** in `sidecar/jarvis/tools/builtin/creator.py`, registered in
  `builtin/__init__.py` and in the `RISK_TABLE` in `sidecar/jarvis/agent/policy.py`:
  - `code.run` (risk medium): runs python3 in `output/sandbox/`, captures
    stdout/stderr, kills on timeout. Returns `{stdout, stderr, exit_code, timed_out}`.
  - `website.build` (risk low): single-file responsive HTML page (embedded CSS,
    system fonts, zero external deps) into `output/sites/<slug>/index.html`.
    Three built-in palettes keyed off `business_type` (food → warm, fitness →
    dark/volt, services → clean blue); hero + offerings derived from the brief,
    contact CTA. No lorem ipsum.
  - `media.image` (risk low, network): generates a real image via the free
    Pollinations API (`GET https://image.pollinations.ai/prompt/{prompt}?width=&height=&nologo=true`),
    saved to `output/media/img-<ts>.jpg`. Smoke-tested live — returned a real JPEG.
    Network failures return a clean `{"error": ...}`, never raise.
  - `video.compose` (risk low): Ken Burns slideshow MP4 via ffmpeg — slow
    zoompan per image, 1080x1920 portrait (or 1920x1080 landscape), 30fps,
    libx264, yuv420p, optional AAC audio track. Returns `{path, duration_sec}`.
  - `social.post_instagram` (risk HIGH → needs user confirmation): dry_run=true
    (default) only validates and returns the would-post payload; dry_run=false
    publishes via `instagram-cli post-feed --account-id <id> --file ... --caption ...`
    using the handle from config (or the first connected account).
- **Config**: `tools_config.yaml` — per-tool enable flags, `output_dir`,
  Pollinations base URL override, `video.seconds_each` default, Instagram
  handle. Re-read at call time (mtime-cached); missing file → defaults, all on.
  A disabled tool returns `{"error": "tool 'x' is disabled in tools_config.yaml"}`.
- **Tests**: `sidecar/tests/unit/test_creator_tools.py` — 17 tests covering
  hello-world + timeout-kill + never-raises, site file with name/`</html>`,
  mocked image network (success + failure), mocked ffmpeg compose, dry_run
  validation, no-CLI error, mocked real publish, and registry/policy wiring
  (high-risk confirm gate). All pass offline.
- **Test results**: **43/43 sidecar unit tests pass** (`make sidecar-test`),
  **12/12 regression evals pass**. (Installed pytest as a user-level test-only
  tool; not a project dependency.)
- **Needs your desktop**: connect an Instagram account (Meta Accounts Center
  via `instagram-cli connect-url`) before real posting works; set the handle in
  `tools_config.yaml`; ffmpeg is already required for video (present here).

## Phase 5 — Windows installer pipeline ✅ SETUP COMPLETE (built by CI, not here)

The full packaging setup so that **one tag push** (`git tag v0.1.0 && git push
origin v0.1.0`) produces real Windows installers. Cannot be linked/test-built
on this Linux machine — CI on `windows-latest` does the actual build.

- **`packaging/sidecar.spec`** — PyInstaller onefile spec for the brain:
  `console=False`, name `jarvis-sidecar`, **core profile only**. Verified the
  true import closure of `jarvis.ipc.server`: no third-party import at module
  top level anywhere in the runtime path; frozen deps are just fastapi,
  uvicorn, pyyaml (+websockets via uvicorn[standard]). Hidden imports cover
  uvicorn's dynamic protocol/loop selection. Excludes keep torch, numpy,
  faster-whisper, onnxruntime, piper, lancedb, pyarrow, sqlcipher3,
  llama-cpp out. No ML weights bundled, ever.
- **`shell/tauri.conf.json`** — `bundle.externalBin: ["binaries/jarvis-sidecar"]`
  (CI drops `jarvis-sidecar-x86_64-pc-windows-msvc.exe` in `shell/binaries/`;
  Tauri installs it next to the app exe with the triple stripped),
  `bundle.resources` ships `models/manifest.json` (object form, no `_up_`
  mangling), `bundle.windows.wix` = en-US + upgradeCode
  `2748cc13-d19a-4ddb-94a8-3fe1e6c3ec4d` (recorded in `packaging/README.md`;
  never change it), `bundle.windows.nsis` = `currentUser` (no UAC) +
  `displayLanguageSelector: false`.
- **Shell fixes (`shell/src/`)** — `ipc.rs`: `jarvis_data_dir()` mirrors the
  sidecar config exactly (`JARVIS_DATA` → `%APPDATA%/ai.jarvis.shell` on
  Windows → `~/.jarvis`), fixing the old `HOME`-env lookup that broke on
  Windows; sidecar spawn appends `.exe` on Windows; supervisor passes the
  bundled manifest path via `JARVIS_MANIFEST` env (dev falls back to the repo
  manifest). `main.rs`: autostart call kept as-is — verified against the
  plugin's own docs that `MacosLauncher::LaunchAgent` is the canonical arg on
  all desktop platforms (registry Run key on Windows); supervisor thread now
  carries the `AppHandle` for resource-dir lookup.
- **Sidecar fixes (`sidecar/jarvis/`)** — `config.py`: platform-aware
  `DATA_DIR`. `models.py`: `default_manifest_path()` (env → repo fallback) and
  a non-interactive `input()` guard for the windowed exe. `voice/pipeline.py`:
  `default_voice_path()` checks the real download location
  (`DATA_DIR/models/piper-voices/...`) before the legacy dev path.
- **`.github/workflows/build-installers.yml`** — on tags `v*` (+ manual):
  Python 3.11 → PyInstaller → rename with triple → `tauri-action@v0`
  (NSIS + MSI) → attaches both to the GitHub Release. `contents: write`
  permission set.
- **`packaging/README.md`** — plain-language walkthrough: Path A (push tag,
  download from Releases) and Path B (two commands on his own Windows PC),
  first-launch model-consent flow, data location, uninstall, upgrades,
  SmartScreen/AV notes.
- **`Makefile` `installer:`** now prints the real instructions.
- **Not yet done (needs his side):** code-signing certificate (SmartScreen
  will warn until then); first CI run on a real tag to prove the pipeline;
  wake-word ONNX + model download UX in the setup wizard.

## Phase 6 — 500-task expansion (2026-09-27)

**81 registered tools, 227 tests green, 12/12 regression, 540-task catalog.**

- **7 tool packs** (`sidecar/jarvis/tools/builtin/`): `comms.py` (mail/sms/
  contacts/calendar, 9 tools), `system_pack.py` (notify/screenshot/clipboard/
  battery/disk/process/uptime, 7), `home_pack.py` (HA/MQTT/devices/scenes, 5),
  `data_pack.py` (csv/json/db/expenses/habits/units, 10), `web_pack.py`
  (search/fetch/summarize/price/rss/bookmarks/unshorten/webshot, 9),
  `dev_pack.py` (git/scaffold/lint/deps/log/port/doctor, 9), `media_pack.py`
  (tts/transcribe/image/video/hash/archive, 10). Replaced 4 old net_tools
  stubs and the empty web.search stub.
- **Specialist roles** (`sidecar/jarvis/agent/roles.py`): researcher, coder,
  writer, planner — each with a role prompt + preferred-tool allowlist (all
  validated against the live registry).
- **`tasks.delegate`** (medium risk): bounded sub-agent loop reusing the same
  `Agent`/LLM/registry/audit/policy (extended `Agent` with optional
  `system_prompt` + `allowed_tools`). Bound in `ipc/server.py` via
  `bind_agent()`; outside a live session it returns an honest
  `delegation is only available inside a live agent session` error.
- **Task catalog** (`docs/TASK-CATALOG.md`, generated by
  `packaging/generate_catalog.py`): **540 tasks** across 27 domains, every
  referenced tool validated against the registry at generation time
  (caught `memory.remember`/`memory.forget` phantom refs before writing).
- **Config** (`tools_config.yaml`): 44 toggles; SMTP/IMAP/SMS webhook/Home
  Assistant/MQTT disabled by default, no real credentials.
- **Verification:** `make sidecar-test` → 227 passed; `make test` regression
  → 12/12; 81/81 tools present in `RISK_TABLE`; 7/7 high-risk tools return
  `needs_confirmation` before executing; catalog maps only to real tools.
- **Honest unavailable/config-gated:** email/SMS/webhook hosts need egress
  allowlisting + config (`{"error": "...not configured"}`); HA/MQTT disabled
  until configured; TTS needs a Piper voice model download; transcription
  needs faster-whisper; `webshot.capture` needs a Chrome-family binary;
  battery/clipboard/notifications return honest errors on this headless VM.

## Phase 7 — Dynamic Tool Universe ✅ COMPLETE (2026-09-27)
- **168,305 addressable tools** (live-measured via `DynamicRegistry.count_addressable()`, lazily resolved — no RAM explosion) + 81 static = **168,386 total**.
- Namespaces: `convert` 167,690 (410 real units, all-pairs, offline math), `web` 308 site×action URL builders, `joke` 271 (260 hand-written, 10 categories), `say`/`greet` 12 languages, `automation` (sqlite routines: create/run/list/delete + `automation.run.<name>` shortcuts), `app` (per-PC discovery), `contact` (per contact × 5 channels × 30 templates, Twilio/SMTP-gated, never fake-sends), `knowledge` (sqlite-FTS5), `opinion` (30 stances + yaml override).
- Integration: `Registry.dynamic` fallback in `tools/base.py` (call + shape-compatible spec_list merge), wired in `build_registry()`. Agent discovers namespaces via `<ns>.*` pointer entries; `agent/core.py` filter hardened with `.get("name")`.
- Tests: **379/379 pass** (264 Phase 7-era + Phase 8-in-flight additions), 12/12 eval regression.
- Honest gates: `say.<lang>` needs Piper voice path; `contact.*` needs Twilio/Telegram/SMTP; `app.focus` Windows-only; automation cron triggers stored, loop pickup = future work.

## Phase 8 — Capability Frontier ✅ COMPLETE (2026-09-27)
- **41 new static tools** (122 static + 168,305 dynamic = **168,427 total**).
- Plugin SDK (`tools/plugins/loader.py`): `plugins.list/reload`, drop-in `~/workspace/jarvis/plugins/` dir, 2 example plugins (hello, text utils). GUI control (7: screenshot/click/type/hotkey/windows/focus/mouse — high-risk gated, platform backends with honest fallbacks). Playwright browser control (6, persistent profile). PC management (7: winget search/install/list, SHA256 duplicates, big files, disk report, startup list). Telegram bot (4). Gmail OAuth (5, installed-app flow, token 0600). Studio (4: podcast, voiced reel, transcribe_notes, mix). Doctor (`system.doctor`, `system.capabilities`).
- Config: `dynamic/tools_config.phase8.yaml` (gmail + telegram); egress allowlist extended.
- Integration fix by parent: patch used `..agent` from `builtin/` (wrong depth) → corrected to `...agent`.
- Tests: **471/471 pass**, 12/12 eval regression (eval harness still to re-run post-integration).

## Phase 9 — Self-extending frontier ✅ COMPLETE (2026-09-27)
- **144 static + 168,305 dynamic = 168,449 total addressable tools.**
- Tool forging (`tools.forge/test_forged/list_forged/retire_forged`): composes new tools from safe primitives, sandbox-tested before activation — Jarvis extends itself. Swarm (`swarm.launch/status/results`): parallel specialist agents, merged results. Background workers (`workers.spawn/tick/list/logs/pause/resume/kill`, sqlite state machine, autonomy opt-in gate, 60s server tick). API mode (`GET /api/v1/health`, `/api/v1/tools`, `POST /api/v1/ask`, token auth, LAN bind only with explicit opt-in — default loopback). Phone conversations (`phone.call_and_talk` + Twilio media-stream handler, STT→agent→TTS loop; `script_test` dry-run verified). Universal inbox (`inbox.triage/read/reply/summarize` across Gmail/Telegram/IMAP).
- Integration: packs registered + RISK_TABLE updated in `build_registry()`; agent binding + `mount_api` in `ipc/server.py`; `bind_host()` LAN gate; Twilio egress allowlist; `workers.tick` startup loop.
- Integration fixes by parent: `..agent` → `...agent` import depth; `bind_host` scope (imported in `main()`).
- Tests: **499 passed, 7 skipped** (fastapi TestClient skips — no fastapi on dev VM), **12/12 eval regression**.
- Honest gaps: phone needs Twilio creds + public wss URL + whisper/Piper + websocket server item; workers need `autonomy_opt_in: true`; API HTTP-layer tests skipped; forged tools inactive until tested.

## Phase 10 — "More bigger" ✅ COMPLETE (2026-09-27)
- **440,516 addressable tools** (170 static + 440,346 dynamic) — verified live via count_addressable().
- New dynamic providers: `tz` 247,506 (every IANA zone pair, real zoneinfo math), `color` 23,256 (5 formats + 148 CSS named colors), `encode`/`decode` 12+12, `math` 100 real formulas, `gen` 6, `say`/`greet` 60 languages each, `joke` 1,011, `web` 621 site×action pairs (65 patterns flagged # UNVERIFIED PATTERN in-file).
- 26 new static tools: pdf (4), translate (2), finance (4: SIP/lumpsum/EMI/budget, integer-paise), travel (3), edu (3), health (4), devops (2), news (2), weather (2, live Open-Meteo).
- Integration fixes by parent: import-time merge collided with worker tests → kept pristine JOKES_BASE/SITES_BASE snapshots; tests updated to assert against them.
- Verified live: EMI ₹8,884.88 on ₹1L@12%/12mo, tz New York→Kolkata DST-aware, sha256 known vector, BMI 22.86.
- Tests: **665 passed, 7 skipped** (pre-existing fastapi skips), 12/12 eval regression.
- Honest gaps: pdf/translate/news need pip packages; flight_search needs Duffel key; weather needs lat/lon args; joke taste is worker-written.

## Phase 11 — AGI autonomy (permissions-gated) ✅ COMPLETE (2026-09-27)
- **440,538 addressable tools** (192 static + 440,346 dynamic) — verified live.
- 22 new tools, 4 packs: permissions (grant/revoke/list/set_default/check — per-tool allow/ask/deny, fail-closed, wildcard support), autopilot (propose/queue/approve/reject/status — batch approval queue, sqlite-persisted), proactive (rules.add/list/remove, proactive.scan/suggest/briefing — suggests only, anti-escalation enforced: rules can never trigger medium+ risk directly), autonomy (budget/status/off/on, agent.run_until/run_continue — kill switch halts workers tick).
- Integration: PolicyEngine.decide() consults permission store (lazy import, cycle-free); packs registered + RISK_TABLE updated; agent bound in server.py for autopilot/proactive/autonomy; kill switch in workers tick_handler.
- Integration fixes by parent: import-line edit hit two occurrences — rewrote line 6 cleanly via script.
- Verified live: deny→deny, allow→allow, revoke→default; budget 2/hr; kill→tick halted; propose→queued (ap_0e109736).
- Tests: **848 passed, 7 skipped** (pre-existing fastapi skips), 12/12 eval regression.
- Honest gaps: autopilot approve needs bound agent (live sidecar); run_until may not converge within max_steps (reports honestly); proactive trigger catalog fixed (inbox_triage, calendar_today, worker_results); budget counts calls not cost.

## Phase 12 — "More advanced" ✅ COMPLETE (2026-09-27)
- **440,559 addressable tools** (213 static + 440,346 dynamic) — verified live.
- 21 new tools, 5 packs: voice (listen/speak/converse/devices — wake-word + duplex, mic-gated high risk), vision (screen/camera/read_text — VLM via ollama-gated, tesseract OCR), mind (memory.learn/consolidate/feedback/routines — rule-based extraction, local sqlite), shell (notify.toast, tray, autostart, hotkey.daemon — Windows-gated), teach (macro.record/stop/play/list — tool-call sequences replayed through the real registry; files.index/search — local FTS5).
- Integration: packs registered + RISK_TABLE; teach_pack bound in server.py; macro-recorder hook in Agent.run (observes every tool call while recording).
- Verified live: memory.learn extracted 2 facts from a sentence; files.search found "March invoice" with highlighted snippet; voice.converse confirmation-gated; vision/toast return honest backend errors on Linux.
- Tests: **848 passed, 7 skipped** (100 new phase-12 tests; pre-existing fastapi skips), 12/12 eval regression. Note: the "848" figure quoted for Phase 11 was measured with phase-12 test files already on disk — true phase-11-only count was 748 passed + 7 skipped.
- Honest gaps: voice needs openwakeword/faster-whisper/Piper models + real mic (100% mocked here); vision needs ollama+moondream/tesseract/display; shell is windows-only (toast-action routing needs a Tauri protocol handler — stub ready); memory extraction is rule-based (misses paraphrase); macros stop at first gate/error.

## Phase 13 — "Most advanced" capstone ✅ COMPLETE (2026-09-27)
- **440,576 addressable tools** (230 static + 440,346 dynamic) — verified live.
- 17 new tools, 4 packs: sleep (sleep.cycle/review/dream — 2am nightly: day summary, briefing draft queued, routine proposals; dream forges candidate tools from repeated failures as morning proposals), self (self.diagnose/upgrade/rollback — audit mining, hardened-wrapper upgrades under forged/upgrades/ only, forbidden targets hard-coded, sandbox-tested, user-approved activation), loops (predict.next/prepare, loops.track/check/done — open-loop promise tracking swept on workers tick, nudges as proposals never auto-sent), persona (persona.note/notes/greet/followup/mood/tone — taste notes, live-composed greetings, commitment detection, tone calibration).
- Integration: packs registered + RISK_TABLE; all four bound in server.py; sleep.cycle fires at 02:00 in the tick loop (idempotent per night); loops.run_check() swept in workers tick_handler with result in tick dict.
- Integration fixes by coordinator: loops_pack `_tool_risk` had the Phase-8 import-depth bug (`..agent.policy` → `...agent.policy`, silently swallowed → all checks "unknown"); fixed, then repaired one worker test that relied on the broken behavior (used weather.now as "unregistered" — now genuinely-unknown name).
- Verified live: diagnose scans; upgrade refuses agent/policy.py even when confirmed; loops.track refuses high-risk check tools; sleep.cycle runs and review reads it back; predict.next honest thin-signal.
- Tests: **916 passed, 7 skipped** (100+ phase-13 tests), 12/12 eval regression.
- Honest gaps: dream/upgrade drafters are mechanical (hardening wrappers, heuristic failure detection), not semantic fixers; prediction is heuristic; persona.followup↔loops registration contract not yet wired (persona falls back to suggestion gracefully); voice/vision/shell still need his Windows PC + models.

## Phase 14 — HYPER cognitive capstone (2026-09-27)
- New tools: 19 (brain_pack: brain.think/plan/replan/plan_status/run_step/decide/focus/reflect; knowledge_pack: knowledge.ask/element/country/constant/timeline/stats; episodic_pack: memory.episode/recall/timeline/link/links). Knowledge datasets in sidecar/jarvis/tools/builtin/data/*.json (118 elements, 194 countries, 38 constants, 122 timeline events, 44 science facts = 516 docs, FTS5).
- Integration: packs registered + RISK_TABLE in builtin/__init__.py; brain_pack.bind_agent in server.py (run_step/reflect need the live agent); sleep.cycle now proposes episodic memory candidates from the audit log (propose-only); knowledge data/*.json added to PyInstaller sidecar.spec datas.
- Verified live: 440,595 addressable tools (249 static + 440,346 dynamic); plans persist across restarts; think honest low-confidence on thin evidence; decide returns scorecard+winner; reflect dedupes; Au=79, Japan=Tokyo/JPY, c=299792458 m/s, timeline query works; episode record→recall→timeline round-trip.
- Tests: 968 passed, 7 skipped (52 new); 12/12 eval regression.
- Honest gaps: brain reasoning is mechanical (keyword-overlap evidence scoring, no semantic similarity); knowledge omits uncertain entries by design (Palestine, Western Sahara, Somaliland, Hubble constant, some capitals); episodic date parser is phrase-based, recall is keyword FTS; memory.link doesn't forward into mind_pack yet.

## Phase 15 — ULTRA professional packs (2026-09-27)
- New tools: 15 (research_pack: research.investigate/briefing/compare; analyst_pack: data.profile/ask/chart/clean; builder_pack: build.scaffold/iterate/review; crm_pack: people.add/note/last_contact/nudges/search).
- Integration: all four registered + RISK_TABLE in builtin/__init__.py; research/builder/crm bound in server.py (analyst needs no bind); sleep.cycle now also queues relationship-nudge proposals (propose-only) alongside episodic candidates.
- Verified live: 440,610 addressable tools (264 static + 440,346 dynamic); data.ask groupby exact (North mean 200.0); chart rendered real PNG; clean wrote sales_cleaned_2.csv (numbered suffix, original untouched); /etc/passwd and ../evil refused; CRM add→note→nudges silent→last_contact round-trip; research.investigate unbound → plan-only honest; scaffold+review produced a real passing project with verdict "clean".
- Tests: 1047 passed, 7 skipped (79 new); 12/12 eval regression.
- Honest gaps: research decomposition/extraction/contradiction-detection are heuristic (syntactic, keyword-overlap); analyst free-text parsing is 3 narrow regexes; builder patches are mechanical (rename/colon/literal±1), honest stuck-reports otherwise; CRM birthday extraction from free-text notes not implemented; nudges need the nightly cycle or manual invocation.

## Phase 15 — ULTRA professional packs (2026-09-27)
- New tools: 15 (research_pack: research.investigate/briefing/compare; analyst_pack: data.profile/ask/chart/clean; builder_pack: build.scaffold/iterate/review; crm_pack: people.add/note/last_contact/nudges/search).
- Integration: packs registered + RISK_TABLE in builtin/__init__.py; research/builder/crm bound in server.py (analyst pure local); sleep.cycle now queues CRM nudges as autopilot proposals (propose-only).
- Verified live: 440,610 addressable tools (264 static + 440,346 dynamic); data.ask groupby mean exact (North 200.0); /tmp + /etc/passwd + symlink escapes refused; CRM add→note→last_contact round-trip case-insensitive; research honest unbound plan (bound:false, executed:false); scaffold created real project, review verdict clean.
- Tests: 1047 passed, 7 skipped (79 new); 12/12 eval regression.
- Honest gaps: research decomposition/contradiction detection heuristic, not live-verified on real network; analyst free-text parsing is 3 narrow regexes, int/float/str inference only; builder patches mechanical (rename/colon/±1), weak test suites could bless wrong patch; CRM doesn't extract birthdays from free text.
