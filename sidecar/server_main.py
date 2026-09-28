"""Frozen-exe entry point for the JARVIS sidecar.

Why this file exists: PyInstaller runs the entry script as __main__ with
no parent package, so an entry script must NOT use relative imports.
jarvis/ipc/server.py starts with `from ..config import ...`, which is why
the frozen exe died with "attempted relative import with no known parent
package". This shim uses only absolute imports; all real code stays in
the jarvis package where relative imports work normally.
"""
from jarvis.ipc.server import main

if __name__ == "__main__":
    main()
