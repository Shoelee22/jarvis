/* J.A.R.V.I.S. Mark IV HUD — sidecar client per proto/contracts.md.
   Add ?demo=1 for the cinematic preview (no sidecar needed). */
(function () {
  "use strict";

  const SIDECAR = "http://127.0.0.1:8765";
  const WS_URL = "ws://127.0.0.1:8765/v1/audio";
  const DEMO = new URLSearchParams(location.search).has("demo");
  const SHOT = new URLSearchParams(location.search).has("shot"); // staged still for screenshots
  const tauri = window.__TAURI__ || null;

  const $ = (id) => document.getElementById(id);
  const messagesEl = $("messages"), coreEl = $("core"), stateLabel = $("core-state-label");
  const inputEl = $("input"), statusDot = $("sidecar-status");

  let token = null, sessionId = null, ws = null, audioCtx = null, micStream = null;
  let waveState = "idle", waveT = 0;

  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const stamp = () => new Date().toTimeString().slice(0, 8);

  /* ---------------- boot sequence ---------------- */
  const BOOT_LINES = [
    ["J.A.R.V.I.S. OS v4.2.1 — local build", ""],
    ["Initializing core systems", "ok"],
    ["Neural interface", "ok"],
    ["Voice synthesis [en_GB · Alan]", "ok"],
    ["Sensor array", "ok"],
    ["Security protocols", "warn"],
  ];
  function runBoot(done) {
    const box = $("boot-lines"), fill = $("boot-fill");
    let i = 0;
    function next() {
      if (i < BOOT_LINES.length) {
        const [text, cls] = BOOT_LINES[i];
        const div = document.createElement("div");
        div.innerHTML = "&gt; " + esc(text) + ' <span class="' + cls + '">' + (cls === "warn" ? "ARMED" : cls === "ok" ? "OK" : "") + "</span>";
        box.appendChild(div);
        fill.style.width = ((i + 1) / BOOT_LINES.length * 100) + "%";
        i++;
        setTimeout(next, DEMO ? 260 : 420);
      } else {
        setTimeout(() => { $("boot").classList.add("done"); done(); }, 500);
      }
    }
    // In the real app the boot is quick; in demo it's the show.
    // ?shot=1 skips straight to a staged HUD for screenshots.
    if (!DEMO || SHOT) { $("boot").classList.add("done"); done(); return; }
    next();
  }

  /* ---------------- state + waveform ---------------- */
  function setState(s) {
    coreEl.className = s;
    stateLabel.textContent = s === "thinking" ? "processing" : s;
    waveState = s;
  }

  const wave = $("wave"), wctx = wave.getContext("2d");
  function drawWave() {
    waveT += 0.03;
    const W = wave.width, H = wave.height, cx = W / 2, cy = H / 2;
    wctx.clearRect(0, 0, W, H);
    const N = 72, baseR = 118;
    const amp = { idle: 6, listening: 34, thinking: 16, speaking: 24 }[waveState] || 6;
    const rot = waveState === "thinking" ? waveT * 0.9 : 0;
    for (let i = 0; i < N; i++) {
      const a = (i / N) * Math.PI * 2 + rot;
      const n = Math.sin(i * 0.7 + waveT * (waveState === "listening" ? 6 : 2.2)) *
                Math.cos(i * 0.23 - waveT * 1.4);
      const len = 3 + Math.abs(n) * amp;
      const r1 = baseR, r2 = baseR + len;
      wctx.strokeStyle = waveState === "idle" ? "rgba(53,224,255,0.35)" : "rgba(53,224,255,0.85)";
      wctx.lineWidth = 2;
      wctx.beginPath();
      wctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
      wctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
      wctx.stroke();
    }
    // faint orbit rings
    wctx.strokeStyle = "rgba(53,224,255,0.12)";
    wctx.lineWidth = 1;
    [150, 168].forEach((r) => { wctx.beginPath(); wctx.arc(cx, cy, r, 0, Math.PI * 2); wctx.stroke(); });
    requestAnimationFrame(drawWave);
  }

  /* ---------------- clock + vitals ---------------- */
  function tickClock() {
    $("clock").textContent = new Date().toTimeString().slice(0, 8);
  }
  function tickVitals() {
    if (DEMO) {
      const p = 96 + Math.round(Math.random() * 3);
      $("v-power").textContent = p + "%";
      $("v-power-bar").style.width = p + "%";
      const l = 8 + Math.round(Math.random() * 14);
      $("v-load").textContent = l + "%";
      $("v-load-bar").style.width = l + "%";
    }
  }

  /* ---------------- dialogue log ---------------- */
  function addLog(kind, who, text, cls) {
    const div = document.createElement("div");
    div.className = "log " + kind;
    div.innerHTML = `<span class="t">${stamp()}</span><span class="who">${who}:</span> <span class="${cls || ""}">${text}</span>`;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
  const sayJarvis = (t) => addLog("jarvis", "JARVIS", esc(t));
  const saySir = (t) => addLog("sir", "SIR", esc(t));
  const saySys = (t, cls) => addLog("sys", "SYS", esc(t), cls);

  /* ---------------- sidecar client ---------------- */
  async function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers || {});
    if (token) opts.headers["Authorization"] = "Bearer " + token;
    if (opts.body && !opts.headers["Content-Type"]) opts.headers["Content-Type"] = "application/json";
    const res = await fetch(SIDECAR + path, opts);
    if (!res.ok) throw new Error("sidecar " + res.status);
    return res.json();
  }
  async function initToken() {
    if (tauri && tauri.core && tauri.core.invoke) {
      try { token = await tauri.core.invoke("get_sidecar_token"); } catch (e) {}
    }
  }
  async function checkHealth() {
    try {
      const h = await api("/v1/health");
      // Status dot: cyan = sidecar reachable. Brain state shown separately.
      // h.brain.status: ready | loading | downloading | missing | failed
      const brain = h.brain || {};
      if (brain.status === "ready") {
        statusDot.className = "status-dot online";
        statusDot.title = "Sidecar online — local brain ready";
      } else if (h.status === "ok") {
        statusDot.className = "status-dot degraded";
        statusDot.title = "Sidecar online — brain: " + (brain.status || "unknown") +
          ". Open Settings → Models to set up the local brain.";
      } else {
        statusDot.className = "status-dot offline";
      }
      return true;
    } catch (e) {
      statusDot.className = "status-dot offline";
      statusDot.title = "Sidecar unreachable";
      return false;
    }
  }

  async function sendChat(text) {
    text = text.trim();
    if (!text) return;
    saySir(text);
    inputEl.value = "";
    setState("thinking");
    if (DEMO) return demoReply(text);
    try {
      const res = await api("/v1/chat", { method: "POST", body: JSON.stringify({ text, session_id: sessionId }) });
      (res.tool_calls || []).forEach((tc) => {
        if (tc.needs_confirmation) renderConfirm(tc);
        else saySys(`▸ ${tc.name} ${tc.ok ? "— ok" : "— failed"}`, tc.ok ? "ok" : "warn");
      });
      if (res.reply_text) {
        setState("speaking");
        sayJarvis(res.reply_text);
        if ((voiceActive || wantVoiceReply) && !DEMO) {
          wantVoiceReply = false;
          speakText(res.reply_text).finally(() => setState("idle"));
        }
        else setTimeout(() => setState("idle"), 1500);
      } else setState("idle");
    } catch (e) {
      setState("idle");
      saySys("sidecar unreachable — restart the app", "warn");
    }
  }

  function renderConfirm(tc) {
    const div = document.createElement("div");
    div.className = "confirm";
    div.innerHTML =
      `<div class="c-title">AUTHORIZATION REQUIRED</div>` +
      `<div class="c-body">${esc(tc.name)} ${esc(JSON.stringify(tc.args || {}))}</div>` +
      `<div class="c-actions"><button class="c-approve">Authorize</button><button class="c-deny">Deny</button></div>`;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    div.querySelector(".c-approve").onclick = async () => {
      div.remove();
      try {
        const r = await api("/v1/tools/call", { method: "POST", body: JSON.stringify({ name: tc.name, args: tc.args, confirmed: true }) });
        saySys(`▸ ${tc.name} ${r.ok ? "— authorized, executed" : "— " + (r.error || "failed")}`, r.ok ? "ok" : "warn");
      } catch (e) { saySys(`▸ ${tc.name} — request failed`, "warn"); }
    };
    div.querySelector(".c-deny").onclick = () => { div.remove(); saySys(`▸ ${tc.name} — denied, sir`, "warn"); };
  }

  /* ---------------- voice ---------------- */
  async function speakText(text) {
    // Spoken replies in voice mode: synthesize server-side (voice-bridge
    // Piper) and play through the existing TTS chunk queue.
    try {
      const r = await api("/v1/voice/tts", { method: "POST",
        body: JSON.stringify({ text: text.slice(0, 500) }) });
      if (r.audio_b64) playTtsChunk(r.audio_b64);
    } catch (e) { saySys("couldn't synthesize the reply — voice.setup may need a re-run", "warn"); }
  }
  function pcm16Processor(stream) {
    const src = audioCtx.createMediaStreamSource(stream);
    const proc = audioCtx.createScriptProcessor(4096, 1, 1);
    const inRate = audioCtx.sampleRate;
    proc.onaudioprocess = (e) => {
      const data = e.inputBuffer.getChannelData(0);
      const out = new Int16Array(Math.floor(data.length * 16000 / inRate));
      for (let i = 0; i < out.length; i++) {
        const s = data[Math.floor(i * inRate / 16000)];
        out[i] = Math.max(-32768, Math.min(32767, s * 32768));
      }
      if (ws && ws.readyState === 1) ws.send(out.buffer);
    };
    src.connect(proc); proc.connect(audioCtx.destination);
    return proc;
  }
  let ttsQueue = Promise.resolve();
  function playTtsChunk(b64) {
    ttsQueue = ttsQueue.then(async () => {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      // Server sends WAV bytes (Piper, 22050 Hz). Decode properly —
      // treating the RIFF header as PCM produced a click + wrong pitch.
      const decoded = await audioCtx.decodeAudioData(bytes.buffer.slice(0));
      const src = audioCtx.createBufferSource();
      src.buffer = decoded; src.connect(audioCtx.destination);
      await new Promise((r) => { src.onended = r; src.start(); });
    }).catch(() => { /* decode failure: skip chunk, stay alive */ });
  }
  async function startVoice() {
    if (DEMO) {
      setState("listening");
      setTimeout(() => { saySir("Jarvis, run a full diagnostic."); sendChat("run a full diagnostic"); }, 1600);
      return;
    }
    // Honest pre-check: the desktop mic flow needs bridge STT + TTS ready.
    // A plain probe (no install, no download) so a tap never hangs.
    try {
      const probe = await api("/v1/tools/call", { method: "POST",
        body: JSON.stringify({ name: "voice.setup", args: { install: false, download: false } }) });
      const ready = probe && probe.result && probe.result.push_to_talk_ready;
      if (!ready) {
        voiceActive = false; setState("idle");
        saySys("voice isn't set up yet — run voice-setup from the JARVIS install folder, then tap the core again", "warn");
        return;
      }
    } catch (e) { /* probe failed: try anyway; the server reports honestly */ }
    try {
      if (tauri && tauri.core && tauri.core.invoke) await tauri.core.invoke("push_to_talk_start");
      const r = await api("/v1/listen/start", { method: "POST", body: JSON.stringify({ mode: "push_to_talk" }) });
      sessionId = r.session_id;
    } catch (e) { saySys("couldn't open a listen session", "warn"); return; }
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    try { await audioCtx.resume(); } catch (e) { /* autoplay policy */ }
    try { micStream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (e) { saySys("microphone blocked — allow mic access or type instead", "warn"); return; }
    setState("listening");
    const stopProc = pcm16Processor(micStream);
    ws = new WebSocket(`${WS_URL}?session_id=${encodeURIComponent(sessionId)}&token=${encodeURIComponent(token || "")}`);
    ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.event === "partial") stateLabel.textContent = "… " + msg.text;
      if (msg.event === "final") {
        // Voice transcript → straight into the chat loop so the brain answers.
        // The reply must be SPOKEN (we're in a voice turn even though the
        // mic is now off), so flag it before sendChat runs.
        const text = (msg.text || "").trim();
        setState("thinking");
        if (text) { wantVoiceReply = true; sendChat(text); }
        else setState("idle");
      }
      if (msg.event === "tool_start") saySys(`▸ ${msg.name} …`);
      if (msg.event === "tool_done") saySys(`▸ ${msg.name} ${msg.ok ? "— ok" : "— failed"}`, msg.ok ? "ok" : "warn");
      if (msg.event === "tts_chunk") { setState("speaking"); playTtsChunk(msg.bytes_b64); }
      if (msg.event === "tts_done") setState("idle");
    };
    ws.onclose = () => { stopVoice(); setState("idle"); };
    coreEl._stopProc = () => { try { stopProc.disconnect(); } catch (e) {} };
  }
  function stopVoice() {
    // Tell the server we're done so it emits `final` BEFORE the socket dies.
    if (ws && ws.readyState === 1) { try { ws.send(JSON.stringify({ event: "stop" })); } catch (e) {} }
    setTimeout(() => {
      if (ws) { try { ws.close(); } catch (e) {} ws = null; }
    }, 400);
    if (coreEl._stopProc) { coreEl._stopProc(); coreEl._stopProc = null; }
    if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
    if (sessionId && !DEMO) api("/v1/listen/stop", { method: "POST", body: JSON.stringify({ session_id: sessionId }) }).catch(() => {});
    voiceActive = false;
    setState("idle");
  }

  /* ---------------- demo script ---------------- */
  function demoReply(text) {
    const low = text.toLowerCase();
    setTimeout(() => {
      if (low.includes("diagnostic")) {
        saySys("▸ tools.diagnostic — all 42 checks passed", "ok");
        setState("speaking");
        sayJarvis("All systems functioning within normal parameters, sir. Power at 98%, neural load nominal. Shall I prepare your evening briefing?");
      } else if (low.includes("briefing")) {
        setState("speaking");
        sayJarvis("Very good, sir. It's 8 PM, clear skies over Delhi at 31 degrees. You have no appointments tomorrow — a rare and suspicious luxury. Anything else?");
      } else if (low.includes("house party")) {
        setState("speaking");
        sayJarvis("The House Party Protocol is armed, sir. Playlists queued, lighting set to 'impress', and I've taken the liberty of hiding the good whiskey.");
      } else if (low.includes("hello") || low.includes("hi")) {
        setState("speaking");
        sayJarvis("At your service, sir. What shall we accomplish this evening?");
      } else {
        setState("speaking");
        sayJarvis("Certainly, sir. Routing that through my local cortex now — full capability unlocks once the sidecar is running.");
      }
      setTimeout(() => setState("idle"), 1800);
    }, 1000);
  }
  function runDemoScript() {
    setTimeout(() => {
      setState("speaking");
      sayJarvis("Good evening, sir. All systems are now operational. How may I assist you?");
      setTimeout(() => setState("idle"), 1600);
    }, 600);
  }

  function stageShot() {
    // Pre-populated cinematic state for the product screenshot.
    $("v-power").textContent = "98%";
    $("v-power-bar").style.width = "98%";
    $("v-load").textContent = "11%";
    $("v-load-bar").style.width = "11%";
    sayJarvis("Good evening, sir. All systems are now operational.");
    saySir("Run a full diagnostic.");
    saySys("▸ tools.diagnostic — all 42 checks passed", "ok");
    sayJarvis("All systems functioning within normal parameters, sir. Power at 98%, neural load nominal. Shall I prepare your evening briefing?");
    setState("speaking");
  }

  /* ---------------- wire up ---------------- */
  let voiceActive = false;
  let wantVoiceReply = false;  // set on WS `final` so the chat reply is spoken
  coreEl.addEventListener("click", () => {
    voiceActive = !voiceActive;
    if (voiceActive) startVoice(); else stopVoice();
  });
  $("btn-send").onclick = () => sendChat(inputEl.value);
  inputEl.addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(inputEl.value); });
  $("btn-settings").onclick = () => { $("settings").classList.toggle("hidden"); refreshModels(); };
  async function refreshModels() {
    const list = $("models-list");
    try {
      const r = await api("/v1/tools/call", { method: "POST",
        body: JSON.stringify({ name: "system.models_status", args: {} }) });
      const res = (r && r.result) || {};
      if (res.error || r.error) { list.innerHTML = `<div class="setting-note">${esc(res.error || r.error)}</div>`; return; }
      list.innerHTML = (res.models || []).map(m =>
        `<div class="vital"><span>${esc(m.id)} <i class="setting-note">${esc(m.role || "")}</i></span>` +
        `<b class="${m.downloaded ? "good" : "warn"}">${m.downloaded ? "READY" : (m.size_gb ? m.size_gb + " GB" : "MISSING")}</b></div>`
      ).join("") || `<div class="setting-note">No models pinned.</div>`;
    } catch (e) { list.innerHTML = `<div class="setting-note">Couldn't reach sidecar.</div>`; }
  }
  $("btn-models-download").onclick = async () => {
    const note = $("models-note");
    note.textContent = "Downloading… the brain model is ~2.5 GB, this takes a while. Progress shows in the list below.";
    try {
      const r = await api("/v1/tools/call", { method: "POST",
        body: JSON.stringify({ name: "system.models_download", args: {} }) });
      const res = (r && r.result) || {};
      if (res.error || r.error) note.textContent = res.error || r.error;
      else if (res.brain) note.textContent = "Brain " + res.brain.status + ": " + (res.brain.detail || "") + " — refresh the list for progress.";
      else { note.textContent = "Download complete — all models verified."; saySys("models updated, sir"); }
    } catch (e) { note.textContent = "Couldn't reach sidecar."; }
    // Poll for progress while a brain download is active.
    let polls = 0;
    const poller = setInterval(async () => {
      if (++polls > 40) { clearInterval(poller); return; }
      try {
        const b = await api("/v1/brain/status");
        if (b.status === "ready" || b.status === "failed") { clearInterval(poller); }
        if (b.status === "downloading") note.textContent = "Downloading brain: " + (b.detail || "");
      } catch (e) {}
      refreshModels();
    }, 15000);
    refreshModels();
  };
  $("btn-camkill").onclick = async () => {
    if (tauri && tauri.core && tauri.core.invoke) {
      await tauri.core.invoke("camera_kill");
      $("btn-camkill").classList.add("armed");
    }
    saySys("camera kill-switch engaged", "warn");
  };
  document.querySelectorAll("#autonomy-seg button").forEach((b) => {
    b.onclick = async () => {
      document.querySelectorAll("#autonomy-seg button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      if (!DEMO) {
        try { await api("/v1/settings/autonomy", { method: "POST", body: JSON.stringify({ level: b.dataset.level }) }); }
        catch (e) { saySys("couldn't reach sidecar", "warn"); }
      }
      saySys(`autonomy → ${b.dataset.level}`);
    };
  });
  document.querySelectorAll(".chip:not(#btn-models-download)").forEach((c) => {
    c.onclick = () => sendChat(c.dataset.routine === "briefing" ? "Give me the morning briefing" :
                               c.dataset.routine === "diagnostic" ? "Run a full diagnostic" :
                               "Initiate the house party protocol");
  });

  (async function init() {
    setState("idle");
    tickClock(); setInterval(tickClock, 1000);
    setInterval(tickVitals, 2500);
    requestAnimationFrame(drawWave);
    runBoot(() => {
      if (DEMO) { if (SHOT) stageShot(); else runDemoScript(); return; }
      initToken().then(async () => {
        const ok = await checkHealth();
        if (ok) { setState("speaking"); sayJarvis("Sidecar link established, sir. All systems nominal. How may I assist you?"); setTimeout(() => setState("idle"), 1800); }
        else saySys("sidecar unreachable — restart the app, or add ?demo=1 to preview", "warn");
        setInterval(checkHealth, 15000);
      });
    });
  })();
})();
