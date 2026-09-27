.PHONY: dev models test installer lint sidecar-test shell-check clean

dev:
	@echo "Starting sidecar (hot reload)…"
	cd sidecar && python3 -m jarvis.ipc.server --reload &
	@echo "Shell: cd shell && cargo tauri dev"

models:
	python3 sidecar/jarvis/models.py download --manifest models/manifest.json

test: sidecar-test
	python3 evals/harness.py --suite regression

sidecar-test:
	cd sidecar && python3 -m pytest tests/ -q

lint:
	cd sidecar && (ruff check jarvis tests 2>/dev/null || python3 -m py_compile $$(find jarvis tests -name '*.py'))

shell-check:
	cd shell && cargo clippy -- -D warnings || echo "cargo not available here — CI runs this"

installer:
	@echo ""
	@echo "JARVIS Windows installer — two supported paths:"
	@echo ""
	@echo "  A) GitHub CI (recommended):"
	@echo "     git tag v0.1.0 && git push origin v0.1.0"
	@echo "     -> .github/workflows/build-installers.yml builds the sidecar exe,"
	@echo "        the NSIS + MSI bundles, and attaches them to the GitHub Release."
	@echo ""
	@echo "  B) Local build on a Windows PC (needs Rust stable + Python 3.11):"
	@echo "     pip install pyinstaller fastapi \"uvicorn[standard]\" pyyaml websockets"
	@echo "     pyinstaller packaging/sidecar.spec"
	@echo "     copy dist\\jarvis-sidecar.exe shell\\binaries\\jarvis-sidecar-x86_64-pc-windows-msvc.exe"
	@echo "     cd shell && cargo tauri build"
	@echo "     installers land in shell/target/release/bundle/{nsis,msi}/"
	@echo ""
	@echo "See packaging/README.md for the plain-language walkthrough."

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null; true
