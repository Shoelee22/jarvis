# Installing JARVIS on your Windows PC

This folder contains everything needed to turn the JARVIS source code into a
real Windows installer (the `.exe` setup file you double-click). You do **not**
need to understand any of the files in here — just pick a path below and
follow the numbered steps.

**WiX upgrade GUID (for maintainers):** `2748cc13-d19a-4ddb-94a8-3fe1e6c3ec4d`
This ID is baked into `shell/tauri.conf.json` (`bundle.windows.wix.upgradeCode`).
Windows uses it to recognise JARVIS upgrades — **never change it**, or
upgrades will install side-by-side instead of replacing the old version.

---

## Path A — GitHub does the work (recommended, ~5 minutes of your time)

You push the code to GitHub once; GitHub's computers build the installer for
you. You just download it.

**One-time setup:**

1. Create a free account at github.com (if you don't have one).
2. On your development machine, in the `jarvis` folder, run:
   ```
   git init
   git add -A
   git commit -m "JARVIS"
   ```
3. Create a new **empty** repository on github.com (do not add a README).
4. Connect and push:
   ```
   git remote add origin https://github.com/<your-name>/jarvis.git
   git push -u origin main
   ```

**Every release (this is the one action that produces an installer):**

5. Tag the version and push the tag:
   ```
   git tag v0.1.0
   git push origin v0.1.0
   ```
6. Wait ~15–25 minutes. Watch progress at
   `github.com/<your-name>/jarvis` → the **Actions** tab.
7. When it finishes, open the **Releases** tab and download
   `JARVIS_0.1.0_x64-setup.exe` (or the `.msi` if you prefer it).
8. Double-click the downloaded file on your Windows PC and follow the
   installer. **No admin rights needed** — it installs just for you.

## Path B — Build it on your own Windows PC

Do this if you'd rather not use GitHub.

1. Install **Rust** from https://rustup.rs/ (accept all defaults).
2. Install **Python 3.11** from https://www.python.org/downloads/
   (tick "Add python.exe to PATH" during setup).
3. WebView2 (what JARVIS draws its window with) is already on Windows
   10/11 — nothing to install.
4. Open PowerShell in the `jarvis` folder and run:
   ```
   pip install pyinstaller fastapi "uvicorn[standard]" pyyaml websockets
   pyinstaller packaging/sidecar.spec
   Copy-Item dist\jarvis-sidecar.exe shell\binaries\jarvis-sidecar-x86_64-pc-windows-msvc.exe
   cd shell
   cargo tauri build
   ```
5. Your installers appear in `shell\target\release\bundle\`:
   `nsis\JARVIS_0.1.0_x64-setup.exe` and `msi\JARVIS_0.1.0_x64_en-US.msi`.
   Double-click either to install.

---

## First launch — the one decision JARVIS asks you

The installer itself is small (tens of MB). JARVIS's brain needs about
**10 GB of AI and voice models**, and these are **never** bundled into the
installer. On first launch JARVIS asks for your permission in its setup
screen:

- **Yes** → it downloads the models once (SHA-256 verified), then runs fully
  offline afterwards.
- **No** → JARVIS still opens and runs in limited mode (chat + tools work,
  voice features stay off until you download the models later from
  Settings → Models).

## Where your stuff lives

- App data, memory, and downloaded models:
  `%APPDATA%\ai.jarvis.shell` (paste that into the File Explorer address bar).
- Upgrades **never** touch this folder — your memory, settings, and models
  survive every update.
- Uninstall: Windows **Settings → Apps → JARVIS → Uninstall**. The app data
  folder above is left behind on purpose; delete it manually if you want a
  fully clean slate.

## Upgrading

Install the newer setup file over the old one — Windows replaces JARVIS in
place and keeps your data. (This works because of the upgrade GUID at the
top of this file.)

## Troubleshooting

- **Windows SmartScreen says "Unknown publisher":** the installer isn't
  code-signed yet (a paid certificate). Click *More info → Run anyway*.
  Your machine, your call.
- **"WebView2 missing" during install:** the installer downloads it
  automatically; you just need internet for that step.
- **JARVIS opens but the mic/voice doesn't work:** the voice models probably
  aren't downloaded yet — check Settings → Models in the app, or ask JARVIS
  to run `voice.setup` (one call installs missing audio backends, downloads
  the pinned voice + speech-recognition models, and self-tests the voice).
- **The sidecar exe was blocked by antivirus:** PyInstaller one-file exes
  occasionally trip heuristic scanners. It's a false positive from bundling
  Python; submitting the file to your AV vendor clears it.
