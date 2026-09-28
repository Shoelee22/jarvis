"""Phase 38 — persistent multi-day coding workspace (offline)."""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import builder_pack


def _use_root(tmp_path):
    os.environ["JARVIS_BUILD_ROOT"] = str(tmp_path / "projects")


def _scaffold(tmp_path, name="demo_proj"):
    _use_root(tmp_path)
    r = builder_pack.build_scaffold_handler(
        {"spec": {"name": name, "kind": "lib",
                  "description": "demo"}})
    assert r.get("ok"), r
    return name


def test_note_and_resume_roundtrip(tmp_path):
    name = _scaffold(tmp_path)
    n = builder_pack.build_note_handler(
        {"project": name, "text": "Decided: use integer paise everywhere."})
    assert n.get("ok"), n
    r = builder_pack.build_resume_handler({"project": name})
    assert r.get("ok"), r
    assert any("integer paise" in b for b in r["recent_notes"])
    assert r["recently_modified"], "scaffolded files should be recent"


def test_resume_surfaces_todo_and_next_step(tmp_path):
    name = _scaffold(tmp_path)
    pdir = builder_pack._project_dir(name)
    mod = pdir / f"{name}.py"
    mod.write_text(mod.read_text() + "\n# TODO: handle edge case\n")
    r = builder_pack.build_resume_handler({"project": name})
    assert r.get("ok"), r
    assert any(t["tag"] == "TODO" for t in r["open_todos"])
    assert r["next_suggested_step"], "should point at the TODO"


def test_note_unknown_project_honest(tmp_path):
    _use_root(tmp_path)
    r = builder_pack.build_note_handler({"project": "nope", "text": "x"})
    assert "error" in r and "no such project" in r["error"]
    r2 = builder_pack.build_resume_handler({"project": "nope"})
    assert "error" in r2


def test_registration_contract():
    names = [s["name"] for s in builder_pack.TOOL_DEFS]
    assert "build.note" in names and "build.resume" in names
    assert builder_pack.RISK_TABLE_ADDITIONS["build.resume"] == (
        "low", False)
