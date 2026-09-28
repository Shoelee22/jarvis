"""Phase 15 (workstream C) — Builder pack tests (offline).

Covers: build.scaffold file trees for cli/web/lib kinds; invalid kind and
path-escape refusal (nothing leaks outside the projects root); scaffold
never overwriting an existing project; build.iterate fixing a SEEDED
off-by-one within max_rounds via a mechanical patch; build.iterate giving
up honestly on an unfixable bug (missing third-party module) with a
confirmation-gated next step and no install attempt; iterate's code.run
path through a bound fake registry; build.review catching a seeded syntax
error and TODO markers; the registration contract (TOOL_DEFS names/risks
match RISK_TABLE_ADDITIONS); _safe_path containment.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import builder_pack
from jarvis.tools.builtin import brain_pack, creator
from jarvis.agent.policy import RISK_TABLE

RISK_TABLE.update(brain_pack.RISK_TABLE_ADDITIONS)  # mirrors build_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the pack at a fresh projects root and unbind the agent."""
    monkeypatch.setenv("JARVIS_BUILD_ROOT", str(tmp_path / "projects"))
    builder_pack.bind_agent(None)
    yield
    builder_pack.bind_agent(None)


def _scaffold(name="demo", kind="lib", desc="demo project"):
    out = builder_pack.build_scaffold_handler(
        {"spec": {"name": name, "kind": kind, "description": desc}})
    assert out["ok"], out
    return out


def _module_file(project, kind):
    root = builder_pack._project_dir(project)
    return {"lib": root / f"{project.replace('-', '_')}.py",
            "cli": root / "main.py",
            "web": root / "app.py"}[kind]


class FakeRegistry:
    """Minimal stand-in: routes code.run at the real creator.code_run."""

    def __init__(self):
        self.calls = []

    def call(self, name, args, actor="agent", confirmed=False, **kw):
        self.calls.append(name)
        if name == "code.run":
            return {"ok": True, "result": creator.code_run(args)}
        if name == "brain.plan":
            return {"ok": True, "result": brain_pack.brain_plan_handler(args)}
        return {"ok": False, "error": "unknown tool " + name}


class FakeAgent:
    def __init__(self, registry):
        self.registry = registry


# ---------------------------------------------------------------------------
# build.scaffold
# ---------------------------------------------------------------------------
def test_scaffold_lib_tree(tmp_path):
    out = _scaffold(name="calc-lib", kind="lib")
    mod = "calc_lib"
    assert out["project"] == "calc-lib"
    assert out["kind"] == "lib"
    assert sorted(out["files"]) == ["README.md", f"{mod}.py",
                                    "tests/__init__.py", f"tests/test_{mod}.py"]
    root = builder_pack._project_dir("calc-lib")
    assert (root / "README.md").read_text().startswith("# calc-lib")
    # the scaffolded tests genuinely pass
    it = builder_pack.build_iterate_handler({"project": "calc-lib"})
    assert it["ok"] and it["tests_run"] == 3, it


def test_scaffold_cli_tree():
    out = _scaffold(name="greeter", kind="cli")
    assert sorted(out["files"]) == ["README.md", "main.py",
                                    "tests/__init__.py", "tests/test_main.py"]
    it = builder_pack.build_iterate_handler({"project": "greeter"})
    assert it["ok"] and it["tests_run"] == 3, it


def test_scaffold_web_tree():
    out = _scaffold(name="site", kind="web")
    assert sorted(out["files"]) == ["README.md", "app.py",
                                    "tests/__init__.py", "tests/test_app.py"]
    it = builder_pack.build_iterate_handler({"project": "site"})
    assert it["ok"] and it["tests_run"] == 3, it


def test_scaffold_invalid_kind():
    out = builder_pack.build_scaffold_handler(
        {"spec": {"name": "x", "kind": "game"}})
    assert not out["ok"] and "unknown kind" in out["error"]


def test_scaffold_refuses_existing():
    _scaffold(name="taken", kind="lib")
    out = builder_pack.build_scaffold_handler(
        {"spec": {"name": "taken", "kind": "lib"}})
    assert not out["ok"] and "already exists" in out["error"]


@pytest.mark.parametrize("bad", ["../evil", "a/b", "..", "x y", "", "x;rm -rf",
                                  "a" * 65, ".hidden"])
def test_scaffold_path_escape_refused(bad, tmp_path):
    out = builder_pack.build_scaffold_handler(
        {"spec": {"name": bad, "kind": "lib"}})
    assert not out["ok"], bad
    assert "refused" in out["error"]
    root = builder_pack._build_root()
    if root.exists():
        # nothing escaped: the root holds no unexpected entries
        assert all(p.name not in ("evil", "b") for p in root.iterdir())


def test_safe_path_refuses_escape(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(ValueError):
        builder_pack._safe_path(base, "..", "evil.py")
    with pytest.raises(ValueError):
        builder_pack._safe_path(base, "sub", "..", "..", "evil.py")
    ok = builder_pack._safe_path(base, "sub", "ok.py")
    assert ok.parent.name == "sub"


# ---------------------------------------------------------------------------
# build.iterate — the fix loop
# ---------------------------------------------------------------------------
def test_iterate_already_green_zero_rounds_of_patches():
    _scaffold(name="clean", kind="lib")
    it = builder_pack.build_iterate_handler({"project": "clean"})
    assert it["ok"] is True
    assert it["tests_run"] == 3
    assert it["changes"] == []


def test_iterate_fixes_seeded_off_by_one():
    _scaffold(name="buggy", kind="lib")
    mod = _module_file("buggy", "lib")
    src = mod.read_text()
    assert "return list(range(n))" in src
    mod.write_text(src.replace("return list(range(n))",
                               "return list(range(n + 1))"))
    it = builder_pack.build_iterate_handler(
        {"project": "buggy", "goal": "fix the off-by-one", "max_rounds": 5})
    assert it["ok"] is True, it
    assert it["rounds_tried"] <= 5
    assert len(it["changes"]) >= 1
    desc = json.dumps(it["changes"])
    assert "mechanical" in desc  # labeled as mechanical, never "understood"
    # the file on disk is actually fixed
    assert "range(n + 1)" not in mod.read_text()
    again = builder_pack.build_iterate_handler({"project": "buggy"})
    assert again["ok"] and again["changes"] == []


def test_iterate_gives_up_honestly_on_missing_module():
    _scaffold(name="doomed", kind="lib")
    mod = _module_file("doomed", "lib")
    mod.write_text("import no_such_module_xyz_9\n" + mod.read_text())
    it = builder_pack.build_iterate_handler({"project": "doomed", "max_rounds": 3})
    assert it["ok"] is False
    assert it.get("stuck") is True
    assert 1 <= it["rounds_tried"] <= 3
    assert "no_such_module_xyz_9" in it["last_error"]
    assert f"tried {it['rounds_tried']} round" in it["note"]
    step = it["next_step"]
    assert step["confirmation_required"] is True
    assert "install package" in step["suggested_next_step"]
    assert "NOT this tool's job" in step["note"]
    # and it never attempted an install: the import is still there
    assert "import no_such_module_xyz_9" in mod.read_text()


def test_iterate_unknown_project():
    out = builder_pack.build_iterate_handler({"project": "ghost"})
    assert not out["ok"] and "no such project" in out["error"]


def test_iterate_uses_code_run_through_bound_registry():
    reg = FakeRegistry()
    builder_pack.bind_agent(FakeAgent(reg))
    _scaffold(name="regtest", kind="lib")
    mod = _module_file("regtest", "lib")
    mod.write_text(mod.read_text().replace(
        "return list(range(n))", "return list(range(n + 1))"))
    it = builder_pack.build_iterate_handler({"project": "regtest", "max_rounds": 5})
    assert it["ok"] is True, it
    assert it["via"] == "code.run"
    assert "code.run" in reg.calls  # went through the live registry
    assert it["plan"].get("plan_id")  # brain.plan bookkeeping happened


def test_iterate_name_error_typo_fix():
    _scaffold(name="typo", kind="cli")
    main = builder_pack._project_dir("typo") / "main.py"
    src = main.read_text()
    # seed: rename the definition, tests import the original name
    main.write_text(src.replace("def greet(who):", "def greetx(who):"))
    it = builder_pack.build_iterate_handler({"project": "typo", "max_rounds": 4})
    assert it["ok"] is True, it
    assert "mechanical typo-style correction" in json.dumps(it["changes"])


# ---------------------------------------------------------------------------
# build.review — static only
# ---------------------------------------------------------------------------
def test_review_clean_project():
    _scaffold(name="tidy", kind="lib")
    rv = builder_pack.build_review_handler({"project": "tidy"})
    assert rv["ok"] is True
    assert rv["verdict"] == "clean"
    assert rv["tests_found"] == 3
    assert rv["test_files"] == 1
    assert rv["issues"] == []


def test_review_catches_seeded_syntax_error_and_todo():
    _scaffold(name="messy", kind="cli")
    pdir = builder_pack._project_dir("messy")
    (pdir / "oops.py").write_text("def broken(:\n    pass\n")
    main = pdir / "main.py"
    main.write_text(main.read_text().replace(
        'return "Hello, "', "# TODO: i18n this\n    return \"Hello, \""))
    rv = builder_pack.build_review_handler({"project": "messy"})
    assert rv["ok"] is True
    syn = [i for i in rv["issues"] if i["type"] == "syntax_error"]
    assert len(syn) == 1
    assert syn[0]["file"] == "oops.py" and syn[0]["line"] == 1
    assert any(t["tag"] == "TODO" and t["file"] == "main.py" for t in rv["todos"])
    assert "no code was executed" in rv["note"]


def test_review_unknown_project():
    out = builder_pack.build_review_handler({"project": "ghost"})
    assert not out["ok"]


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
def test_registration_contract():
    class Reg:
        def __init__(self):
            self.tools = {}

        def register(self, tool):
            self.tools[tool.name] = tool

    reg = Reg()
    builder_pack.register(reg)
    # Later phases extend the pack (build.note, build.resume) — the originals
    # keep their contract; every registered tool matches its TOOL_DEF.
    assert {"build.scaffold", "build.iterate", "build.review"} <= set(reg.tools)
    for spec in builder_pack.TOOL_DEFS:
        tool = reg.tools[spec["name"]]
        assert tool.risk == spec["risk"]
        assert tool.needs_network == spec["needs_network"]
        assert callable(tool.handler)
        risk, net = builder_pack.RISK_TABLE_ADDITIONS[spec["name"]]
        assert (risk, net) == (spec["risk"], spec["needs_network"])
    # handlers never raise on garbage input
    for spec in builder_pack.TOOL_DEFS:
        out = spec["handler"]({"project": "!!!bad!!!", "spec": {"name": ".."}})
        assert isinstance(out, dict)
