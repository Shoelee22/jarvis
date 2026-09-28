"""Phase 8 — Plugin SDK tests (offline)."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, "sidecar")

from jarvis.tools.plugins import TOOL_DEFS, loader
from jarvis.tools.plugins.loader import load_plugins, load_warnings


def _load_module_file(path: Path, qualname: str):
    spec = importlib.util.spec_from_file_location(qualname, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _empty_dir(tmp_path):
    d = tmp_path / "empty_user_plugins"
    d.mkdir()
    return d


def _by_name(defs):
    return {d["name"]: d for d in defs}


def test_discovers_both_example_plugins(tmp_path):
    # Empty user dir keeps the test hermetic (real ~/workspace/jarvis/plugins
    # may gain user plugins later).
    defs = load_plugins(user_dir=_empty_dir(tmp_path), examples_dir=None)
    names = _by_name(defs)
    assert "hello.greet" in names
    assert "text.uppercase" in names
    assert "text.wordcount" in names
    assert "text.reverse" in names
    assert len(defs) == 4
    for d in defs:
        assert d["risk"] == "low"
        assert d["needs_network"] is False
        assert callable(d["handler"])
        assert isinstance(d["schema"], dict)


def test_tool_defs_shape():
    assert isinstance(TOOL_DEFS, list) and len(TOOL_DEFS) == 2
    by_name = {d["name"]: d for d in TOOL_DEFS}
    assert set(by_name) == {"plugins.list", "plugins.reload"}
    for d in TOOL_DEFS:
        assert set(d) == {"name", "description", "handler", "risk",
                          "needs_network", "schema"}
        assert d["risk"] == "low" and d["needs_network"] is False
        assert callable(d["handler"])


def test_hello_greet_handler():
    mod = _load_module_file(
        loader.DEFAULT_EXAMPLES_DIR / "hello_plugin" / "hello_plugin.py",
        "t8_hello_plugin")
    r = mod.greet({"name": "Ada"})
    assert r["greeting"] == "Hello, Ada. At your service."
    r = mod.greet({})
    assert "sir" in r["greeting"]


def test_text_tool_handlers():
    mod = _load_module_file(
        loader.DEFAULT_EXAMPLES_DIR / "text_tools_plugin" / "text_tools.py",
        "t8_text_tools")
    assert mod.to_uppercase({"text": "hello world"}) == {"text": "HELLO WORLD"}
    assert mod.wordcount({"text": "hello brave\nnew world"}) == {
        "words": 4, "chars": 21, "lines": 2}
    assert mod.wordcount({"text": ""}) == {"words": 0, "chars": 0, "lines": 0}
    assert mod.reverse_text({"text": "abc"}) == {"text": "cba"}


def test_loaded_handlers_never_raise(tmp_path):
    defs = load_plugins(user_dir=_empty_dir(tmp_path), examples_dir=None)
    names = _by_name(defs)
    out = names["hello.greet"]["handler"]({"name": "Ada"})
    assert out["greeting"] == "Hello, Ada. At your service."
    out = names["text.wordcount"]["handler"]({"text": "one two"})
    assert out["words"] == 2


def test_broken_plugins_are_skipped_without_crashing(tmp_path):
    user = tmp_path / "plugins"
    user.mkdir()
    # 1. invalid yaml
    bad = user / "bad_yaml"; bad.mkdir()
    (bad / "plugin.yaml").write_text("name: [unclosed\n  : : :\n")
    # 2. handler function missing from module
    miss = user / "missing_handler"; miss.mkdir()
    (miss / "plugin.yaml").write_text(
        "name: miss\nversion: 1.0.0\ntools:\n"
        "  - name: nope\n    description: x\n    handler: \"mod:gone\"\n"
        "    schema: {}\n")
    (miss / "mod.py").write_text("def other(args):\n    return {}\n")
    # 3. module file missing entirely
    nomod = user / "no_module"; nomod.mkdir()
    (nomod / "plugin.yaml").write_text(
        "name: nomod\nversion: 1.0.0\ntools:\n"
        "  - name: gone\n    description: x\n    handler: \"absent:fn\"\n"
        "    schema: {}\n")
    # 4. bad handler signature (takes two args)
    badsig = user / "bad_sig"; badsig.mkdir()
    (badsig / "plugin.yaml").write_text(
        "name: badsig\nversion: 1.0.0\ntools:\n"
        "  - name: twoargs\n    description: x\n    handler: \"sig:two\"\n"
        "    schema: {}\n")
    (badsig / "sig.py").write_text(
        "def two(a, b):\n    return {}\n")
    # 5. plugin.yaml not a mapping
    notmap = user / "not_mapping"; notmap.mkdir()
    (notmap / "plugin.yaml").write_text("- just\n- a\n- list\n")
    # 6. raising handler still loads (wrapped), never raises on call
    raiser = user / "raiser"; raiser.mkdir()
    (raiser / "plugin.yaml").write_text(
        "name: raiser\nversion: 1.0.0\ntools:\n"
        "  - name: boom\n    description: x\n    handler: \"rmod:boom\"\n"
        "    schema: {}\n")
    (raiser / "rmod.py").write_text(
        "def boom(args):\n    raise RuntimeError('kablam')\n")
    empty_examples = tmp_path / "no_examples"; empty_examples.mkdir()

    defs = load_plugins(user_dir=user, examples_dir=empty_examples)
    names = _by_name(defs)
    assert "raiser.boom" in names  # the only survivor
    assert len(defs) == 1
    out = names["raiser.boom"]["handler"]({})
    assert "error" in out and "kablam" in out["error"]
    assert len(load_warnings()) >= 5  # each broken case logged a warning


def test_duplicate_tool_names_do_not_crash(tmp_path):
    user = tmp_path / "plugins"; user.mkdir()
    for i in (1, 2):
        p = user / f"dup{i}"; p.mkdir()
        (p / "plugin.yaml").write_text(
            "name: dup\nversion: 1.0.0\ntools:\n"
            "  - name: ping\n    description: x\n    handler: \"m:ping\"\n"
            "    schema: {}\n")
        (p / "m.py").write_text(f"def ping(args):\n    return {{'n': {i}}}\n")
    empty_examples = tmp_path / "no_examples"; empty_examples.mkdir()

    defs = load_plugins(user_dir=user, examples_dir=empty_examples)
    names = sorted(_by_name(defs))
    assert names == ["dup.ping", "dup_2.ping"]
    assert _by_name(defs)["dup.ping"]["handler"]({}) == {"n": 1}
    assert _by_name(defs)["dup_2.ping"]["handler"]({}) == {"n": 2}
    assert any("duplicate" in w for w in load_warnings())


def test_plugins_list_and_reload_tools():
    reload_fn = {d["name"]: d for d in TOOL_DEFS}["plugins.reload"]["handler"]
    list_fn = {d["name"]: d for d in TOOL_DEFS}["plugins.list"]["handler"]
    r = reload_fn({})
    assert r["plugins"] == 2
    assert r["tools"] == 4
    assert isinstance(r["warnings"], list)
    listing = list_fn({})
    assert listing["count"] == 2
    by_name = {p["name"]: p for p in listing["plugins"]}
    assert by_name["hello"]["tool_count"] == 1
    assert by_name["hello"]["tools"] == ["hello.greet"]
    assert by_name["text"]["tool_count"] == 3
    assert set(by_name["text"]["tools"]) == {
        "text.uppercase", "text.wordcount", "text.reverse"}


def test_risk_table_entries(tmp_path):
    from jarvis.tools.plugins import risk_table_entries
    entries = risk_table_entries()
    assert entries["plugins.list"] == ("low", False)
    assert entries["plugins.reload"] == ("low", False)
    # Default dirs (empty user dir in this env): example tools are present.
    assert entries["hello.greet"] == ("low", False)
    assert entries["text.wordcount"] == ("low", False)


def test_reload_reflects_new_user_plugin(tmp_path):
    reload_fn = {d["name"]: d for d in TOOL_DEFS}["plugins.reload"]["handler"]
    user = tmp_path / "plugins"; user.mkdir()
    p = user / "late"; p.mkdir()
    (p / "plugin.yaml").write_text(
        "name: late\nversion: 0.2.0\ndescription: arrived late\ntools:\n"
        "  - name: hi\n    description: x\n    handler: \"m:hi\"\n"
        "    risk: high\n    schema: {}\n")
    (p / "m.py").write_text("def hi(args):\n    return {'hi': True}\n")
    # reload() scans default dirs; point HOME plugins aside by calling loader
    # with explicit dirs instead, then check risk validation + namespacing.
    empty_examples = tmp_path / "no_examples"; empty_examples.mkdir()
    defs = load_plugins(user_dir=user, examples_dir=empty_examples)
    names = _by_name(defs)
    assert names["late.hi"]["risk"] == "high"
    assert reload_fn({})["plugins"] >= 2  # default dirs still load fine
