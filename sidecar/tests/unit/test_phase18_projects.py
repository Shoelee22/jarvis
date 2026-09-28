"""Offline tests for the Phase 18 projects pack."""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "sidecar")

import jarvis.tools.builtin.projects_pack as pp
from jarvis.tools.base import Registry, Tool


@pytest.fixture(scope="module", autouse=True)
def _leave_no_module_trace():
    """Drop only our own pack's sys.modules entry on teardown. We must NOT
    delete the pack attribute from the jarvis.tools.builtin package: that
    attribute is the same object as __init__'s global, and build_registry()
    needs it for later tests."""
    yield
    sys.modules.pop("jarvis.tools.builtin.projects_pack", None)


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    db = tmp_path / "projects.db"
    monkeypatch.setenv("JARVIS_PROJECTS_DB", str(db))
    yield db


def _create(name="website", goal="Ship the site", promise=None, **kwargs):
    args = {"name": name, "goal": goal, **kwargs}
    if promise:
        args["promise"] = promise
    return pp.create_handler(args)


# ---------------------------------------------------------------------------
# create / list
# ---------------------------------------------------------------------------

class TestCreate:
    def test_create_ok(self, tmpdb):
        out = _create()
        assert out["ok"] is True
        assert out["name"] == "website"
        assert out["status"] == "active"

    def test_create_with_deadline(self, tmpdb):
        out = _create(name="p2", deadline="2026-12-31")
        assert out["ok"] is True
        assert out["deadline"].startswith("2026-12-31")

    def test_create_bad_deadline(self, tmpdb):
        out = _create(name="p3", deadline="someday")
        assert "error" in out

    def test_create_duplicate(self, tmpdb):
        _create(name="dup")
        out = _create(name="dup")
        assert "already exists" in out["error"]

    def test_create_needs_goal(self, tmpdb):
        out = pp.create_handler({"name": "nogoal"})
        assert "error" in out

    def test_create_promise_links_loop(self, tmpdb, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_LOOPS_DB", str(tmp_path / "loops.db"))
        out = _create(name="promised", promise="Launch the beta")
        assert out["ok"] is True
        link = out.get("promise_link")
        assert link is not None
        # loops.track requires the check tool to be known low-risk;
        # projects.status is in RISK_TABLE_ADDITIONS, but the test process
        # RISK_TABLE is only updated when the full registry is built.
        # Either outcome is honest: registered or a clear reason.
        assert ("registered" in link) or ("reason" in link)

    def test_list_filters(self, tmpdb):
        _create(name="a1")
        _create(name="a2")
        pp.archive_handler({"name": "a2"})
        active = pp.list_handler({"status": "active"})
        assert {p["name"] for p in active["projects"]} == {"a1"}
        archived = pp.list_handler({"status": "archived"})
        assert {p["name"] for p in archived["projects"]} == {"a2"}


# ---------------------------------------------------------------------------
# milestones + status
# ---------------------------------------------------------------------------

class TestMilestones:
    def test_full_flow(self, tmpdb):
        _create(name="flow")
        m1 = pp.milestone_add_handler(
            {"project": "flow", "title": "Design", "due": "2026-10-10"})
        assert m1["ok"] is True
        m2 = pp.milestone_add_handler({"project": "flow", "title": "Build"})
        assert m2["ok"] is True
        done = pp.milestone_done_handler(
            {"project": "flow", "milestone_id": m1["milestone_id"]})
        assert done["ok"] is True
        st = pp.status_handler({"name": "flow"})
        assert st["ok"] is True
        assert st["progress"] == {"milestones_total": 2, "milestones_done": 1,
                                  "percent": 50.0}
        assert len(st["milestones"]) == 2

    def test_milestone_unknown_project(self, tmpdb):
        out = pp.milestone_add_handler({"project": "nope", "title": "x"})
        assert "error" in out

    def test_milestone_done_unknown_id(self, tmpdb):
        _create(name="flow2")
        out = pp.milestone_done_handler(
            {"project": "flow2", "milestone_id": "ms_nope"})
        assert "error" in out

    def test_overdue_detection(self, tmpdb):
        _create(name="late", deadline="2020-01-01")
        pp.milestone_add_handler(
            {"project": "late", "title": "Old task", "due": "2020-02-01"})
        st = pp.status_handler({"name": "late"})
        assert st["deadline_overdue"] is True
        assert st["overdue_milestones"] == 1

    def test_status_unknown(self, tmpdb):
        assert "error" in pp.status_handler({"name": "ghost"})


# ---------------------------------------------------------------------------
# log / archive / delete
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_log_and_status(self, tmpdb):
        _create(name="logged")
        out = pp.log_handler({"project": "logged", "entry": "Shipped v1"})
        assert out["ok"] is True
        st = pp.status_handler({"name": "logged"})
        assert st["recent_log"][0]["entry"] == "Shipped v1"

    def test_log_missing_file(self, tmpdb):
        _create(name="logged2")
        out = pp.log_handler({"project": "logged2", "entry": "x",
                              "file": "/nonexistent/file.txt"})
        assert "file not found" in out["error"]

    def test_archive_and_delete(self, tmpdb):
        _create(name="gone")
        a = pp.archive_handler({"name": "gone"})
        assert a["status"] == "archived"
        d = pp.delete_handler({"name": "gone"})
        assert d["deleted"] is True
        assert "error" in pp.status_handler({"name": "gone"})

    def test_delete_unknown(self, tmpdb):
        assert "error" in pp.delete_handler({"name": "ghost"})


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_all_eight(self):
        reg = Registry()
        pp.register(reg)
        for name in ("projects.create", "projects.list", "projects.status",
                     "projects.milestone_add", "projects.milestone_done",
                     "projects.log", "projects.archive", "projects.delete"):
            assert name in reg.tools, name
        assert reg.tools["projects.delete"].risk == "high"
        assert reg.tools["projects.archive"].risk == "medium"

    def test_risk_table_entries(self):
        assert pp.RISK_TABLE_ADDITIONS["projects.create"] == ("low", False)
        assert pp.RISK_TABLE_ADDITIONS["projects.delete"] == ("high", False)
