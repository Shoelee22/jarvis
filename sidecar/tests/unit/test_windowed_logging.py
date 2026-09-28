"""Regression test for the v0.3.0 windowed-mode sidecar crash.

On Windows the installed app runs the frozen sidecar with console=False,
so sys.stderr is None. uvicorn's default LOGGING_CONFIG instantiates
uvicorn.logging.DefaultFormatter, whose __init__ calls sys.stderr.isatty()
-> AttributeError -> "Unable to configure formatter 'default'" -> the
sidecar died on every launch and the shell reported "sidecar unreachable".

main() must therefore pass log_config=None to uvicorn.run().
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jarvis.ipc import server  # noqa: E402


def _run_main_with_mocked_uvicorn(argv):
    """Call server.main() with uvicorn.run patched; return its kwargs."""
    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    with patch.object(sys, "argv", ["server_main.py"] + argv):
        with patch("uvicorn.run", fake_run):
            server.main()
    return captured


def test_main_disables_uvicorn_log_config():
    captured = _run_main_with_mocked_uvicorn(["--port", "18765"])
    assert captured["kwargs"].get("log_config") is None, (
        "main() must pass log_config=None so the windowed frozen sidecar "
        "(sys.stderr is None) does not crash in uvicorn's logging setup"
    )
    assert captured["kwargs"]["port"] == 18765


def test_main_still_uses_app_factory_and_localhost():
    captured = _run_main_with_mocked_uvicorn([])
    assert captured["args"][0] == "jarvis.ipc.server:build_app"
    assert captured["kwargs"]["factory"] is True
    assert captured["kwargs"]["host"] in ("127.0.0.1", "localhost")
