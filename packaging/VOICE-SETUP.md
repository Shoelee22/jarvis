# JARVIS Voice Setup (Windows)

## The easy way: one double-click

1. Get the JARVIS source (clone the repo or unzip the source package).
2. Open the `packaging` folder and **double-click `setup-windows.bat`**.

That's it. The script installs Python if missing, installs every package,
downloads the voice models, runs a self-test, and then speaks out loud so
you can hear it's working. Afterwards, double-click
`packaging\start-jarvis.bat` whenever you want to start him.

If anything fails, the script tells you exactly what went wrong.

## The manual way

Two paths. Path A is the source install with everything: speak, listen,
wake word, barge-in, full conversation. Path B is the desktop installer
app with push-to-talk voice (mic button → transcription → spoken reply).

---

## Path A — Full voice (recommended)

**1. Install Python 3.11+**
Download from python.org. On the first installer screen tick
**"Add python.exe to PATH"**. Then in PowerShell:

```powershell
python --version   # must say 3.11 or newer
```

**2. Install the voice backends**

```powershell
pip install piper-tts faster-whisper openwakeword sounddevice fastapi uvicorn
```

**3. Get the JARVIS source**
`git clone https://github.com/Shoelee22/jarvis` (or unzip the
JARVIS-full-source package), then `cd jarvis`.

**4. Download the voice models** (~140 MB, one time)

```powershell
python sidecar/jarvis/models.py download --manifest models/manifest.json --yes
```

Every file is SHA-256 verified. If a download drops mid-way, just
re-run — failed files are cleaned up and retried automatically.

**5. One-call check**
Start the sidecar and run the setup tool, or run it directly:

```powershell
cd sidecar
python -m jarvis.ipc.server
```

Then ask JARVIS to run `voice.setup`. It installs any missing backend,
downloads any missing model, and runs a real speech self-test. You want
to see `"voice_ready": true`.

**6. Hear him speak**
`voice.speak` with any text — you should hear Alan, the British voice.

**7. Talk to him**
`voice.converse`: say "Jarvis" (wake word), speak your request, and he
answers out loud. You can interrupt him mid-sentence — barge-in stops
his speech and listens to you.

---

## Path B — Desktop installer app (v0.3.0+)

The installer is small on purpose: the ML voice engine (Piper TTS,
faster-whisper STT) runs in a real Python on your machine via the
voice-bridge — nothing is installed globally, nothing phones home.

1. Install the app from the latest GitHub release
   (v0.3.0 or newer — v0.2.x installers are broken, do not use them).
2. Install **Python 3.11+** from https://www.python.org/downloads/ — on the
   first installer screen tick **"Add python.exe to PATH"**, then reboot.
   (Advanced: point the `JARVIS_VOICE_PYTHON` environment variable at any
   Python 3.9+ command, e.g. `py -3.11`.)
3. Launch JARVIS. Tap the reactor core once: the app probes `voice.setup`.
   If Python or a backend is missing you get an honest message naming the
   exact fix — install it, tap again.
4. In the app: Settings → Models → download the voice + STT models
   (~140 MB, one time, SHA-256 verified).
5. Hold the core, speak, release: your speech is transcribed by
   faster-whisper and his reply is spoken aloud by Piper.

Notes & limits (installer app):
- Push-to-talk is the voice path in the app. Wake-word listening and the
  `voice.converse` loop need Path A (source mode with in-process mic).
- The first tap after installing Python may take a few minutes: the
  voice-bridge pip-installs `piper-tts` and `faster-whisper` into that
  Python only.
- If the core says voice isn't set up, open the app log
  (`%APPDATA%\Jarvis\logs`) — the probe result names the missing piece.

---

## Troubleshooting

- **No sound:** check Windows Settings → Sound → output device; some
  machines need the app restarted after plugging in headphones.
- **Mic not heard:** Windows Settings → Privacy → Microphone — allow
  desktop apps to access it.
- **pip blocked ("externally managed"):** `voice.setup` handles this
  automatically; manually, add `--break-system-packages`.
- **pip fails with "Cannot uninstall typing_extensions":**
  `voice.setup` retries with `--ignore-installed` automatically.
- **"Model file is HTML / corrupt":** fixed in current builds — the
  manifest now pins real download URLs with SHA-256 hashes, and
  `voice.setup` re-downloads anything that fails verification.
