"""Phase 11 workstream 1: permission grants store tests.

Everything here runs against a tmp sqlite file via JARVIS_PERMISSIONS_DB.
"""
import pytest

from jarvis.tools.builtin import permissions_pack as pp


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    """Point the pack at a fresh tmp DB and reset all module caches."""
    monkeypatch.setenv("JARVIS_PERMISSIONS_DB", str(tmp_path / "permissions.db"))
    pp._store_cache.update(path=None, conn=None)
    pp._level_cache.update(path=None, levels={})
    pp._TOOL_NAMES_SNAPSHOT.clear()
    yield
    conn = pp._store_cache["conn"]
    if conn is not None:
        conn.close()
    pp._store_cache.update(path=None, conn=None)
    pp._level_cache.update(path=None, levels={})
    pp._TOOL_NAMES_SNAPSHOT.clear()


# ---------------------------------------------------------------------------
# grant / revoke / check semantics
# ---------------------------------------------------------------------------
def test_unset_returns_none(tmpdb):
    assert pp.get_level("web.search") is None
    assert pp.get_level("anything.at.all") is None


def test_grant_allow_ask_deny_semantics(tmpdb):
    assert pp.grant_handler({"tool": "web.search", "level": "allow"})["ok"] is True
    assert pp.get_level("web.search") == "allow"
    assert pp.grant_handler({"tool": "web.search", "level": "ask"})["ok"] is True
    assert pp.get_level("web.search") == "ask"  # upsert overwrites
    assert pp.grant_handler({"tool": "web.search", "level": "deny"})["ok"] is True
    assert pp.get_level("web.search") == "deny"  # fail-closed


def test_grant_stores_actor_and_ts(tmpdb):
    res = pp.grant_handler({"tool": "fs.read", "level": "ask", "actor": "alice"})
    assert res["ok"] is True
    assert res["actor"] == "alice"
    assert isinstance(res["created_ts"], int) and res["created_ts"] > 0
    listed = pp.list_handler({})
    row = [g for g in listed["grants"] if g["pattern"] == "fs.read"][0]
    assert row["level"] == "ask"
    assert row["actor"] == "alice"
    assert isinstance(row["created_ts"], int) and row["created_ts"] > 0


def test_revoke_removes_exact_row(tmpdb):
    pp.grant_handler({"tool": "web.search", "level": "deny"})
    res = pp.revoke_handler({"tool": "web.search"})
    assert res["ok"] is True and res["revoked"] is True
    assert pp.get_level("web.search") is None


def test_revoke_missing_row_errors(tmpdb):
    res = pp.revoke_handler({"tool": "nope.tool"})
    assert "error" in res


def test_wildcard_grant_returns_loud_warning(tmpdb):
    res = pp.grant_handler({"tool": "web.*", "level": "allow"})
    assert res["ok"] is True
    assert "warning" in res
    assert "wildcard grant" in res["warning"].lower()
    assert "covered_tools" in res
    assert pp.get_level("web.search") == "allow"


def test_global_wildcard(tmpdb):
    pp.grant_handler({"tool": "*", "level": "ask"})
    assert pp.get_level("web.search") == "ask"
    assert pp.get_level("fs.write") == "ask"


def test_deny_always_wins_over_looser_wildcards(tmpdb):
    # Global allow + exact deny: the exact deny must win (fail-closed).
    pp.grant_handler({"tool": "*", "level": "allow"})
    pp.grant_handler({"tool": "fs.delete", "level": "deny"})
    assert pp.get_level("fs.delete") == "deny"
    assert pp.get_level("web.search") == "allow"


# ---------------------------------------------------------------------------
# precedence: exact > namespace.* > * > default > None
# ---------------------------------------------------------------------------
def test_precedence_exact_beats_namespace(tmpdb):
    pp.grant_handler({"tool": "web.*", "level": "deny"})
    pp.grant_handler({"tool": "web.search", "level": "allow"})
    assert pp.get_level("web.search") == "allow"  # exact wins
    assert pp.get_level("web.fetch") == "deny"    # namespace still applies


def test_precedence_namespace_beats_global(tmpdb):
    pp.grant_handler({"tool": "*", "level": "deny"})
    pp.grant_handler({"tool": "web.*", "level": "ask"})
    assert pp.get_level("web.search") == "ask"  # ns.* beats *
    assert pp.get_level("fs.write") == "deny"   # * still applies


def test_precedence_global_beats_default(tmpdb):
    pp.set_default_handler({"level": "deny"})
    pp.grant_handler({"tool": "*", "level": "allow"})
    assert pp.get_level("web.search") == "allow"  # * beats default


def test_default_used_when_nothing_matches(tmpdb):
    pp.set_default_handler({"level": "ask"})
    assert pp.get_level("web.search") == "ask"
    check = pp.check_handler({"tool": "web.search"})
    assert check["level"] == "ask"
    assert check["matched_rule"] == "default"


def test_set_default_unset_restores_none(tmpdb):
    pp.set_default_handler({"level": "deny"})
    assert pp.get_level("web.search") == "deny"
    pp.set_default_handler({"level": "unset"})
    assert pp.get_level("web.search") is None


def test_set_default_ask_is_paranoid_mode(tmpdb):
    res = pp.set_default_handler({"level": "ask"})
    assert res["ok"] is True
    assert "paranoid" in res["note"].lower()


def test_set_default_allow_carries_warning(tmpdb):
    res = pp.set_default_handler({"level": "allow"})
    assert "warning" in res


def test_check_shows_which_rule_matched(tmpdb):
    pp.grant_handler({"tool": "web.*", "level": "ask"})
    check = pp.check_handler({"tool": "web.search"})
    assert check["level"] == "ask"
    assert check["matched_rule"] == "web.*"


def test_check_unset_when_nothing_matches(tmpdb):
    check = pp.check_handler({"tool": "web.search"})
    assert check["level"] == "unset"
    assert check["matched_rule"] == "none"


def test_list_shows_grants_and_effective_default(tmpdb):
    pp.grant_handler({"tool": "web.*", "level": "ask"})
    res = pp.list_handler({})
    assert res["default"] == "unset"
    assert any(g["pattern"] == "web.*" and g["level"] == "ask"
               for g in res["grants"])
    pp.set_default_handler({"level": "deny"})
    assert pp.list_handler({})["default"] == "deny"


# ---------------------------------------------------------------------------
# cache + persistence
# ---------------------------------------------------------------------------
def test_cache_invalidated_on_grant(tmpdb):
    # Prime the cache with a lookup.
    assert pp.get_level("web.search") is None
    assert "web.search" in pp._level_cache["levels"]
    # A write must invalidate it; the next read re-resolves from the DB.
    pp.grant_handler({"tool": "web.search", "level": "deny"})
    assert "web.search" not in pp._level_cache["levels"]
    assert pp.get_level("web.search") == "deny"


def test_persistence_across_reload(tmpdb):
    pp.grant_handler({"tool": "web.*", "level": "ask"})
    pp.set_default_handler({"level": "deny"})
    # Simulate a fresh process: drop the connection AND the level cache,
    # keeping the same DB file on disk.
    pp._store_cache["conn"].close()
    pp._store_cache.update(path=None, conn=None)
    pp._level_cache.update(path=None, levels={})
    assert pp.get_level("web.search") == "ask"   # re-read from disk
    assert pp.get_level("fs.write") == "deny"    # default re-read from disk


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "   ", "web search", "web.*.x",
                                 "web*", "*.search", "web..search"])
def test_invalid_tool_pattern_rejected(tmpdb, bad):
    res = pp.grant_handler({"tool": bad, "level": "allow"})
    assert "error" in res, bad
    res = pp.revoke_handler({"tool": bad})
    assert "error" in res, bad


def test_invalid_level_rejected(tmpdb):
    res = pp.grant_handler({"tool": "web.search", "level": "maybe"})
    assert "error" in res
    res = pp.set_default_handler({"level": "maybe"})
    assert "error" in res


def test_handlers_never_raise(tmpdb):
    # Garbage inputs must produce dicts with "error", never exceptions.
    assert "error" in pp.grant_handler(None or {})
    assert "error" in pp.revoke_handler({})
    assert "error" in pp.check_handler({})
    assert "error" in pp.set_default_handler({})
    assert isinstance(pp.list_handler({}), dict)


# ---------------------------------------------------------------------------
# registration contract
# ---------------------------------------------------------------------------
def test_tool_defs_risks_correct(tmpdb):
    risks = {d["name"]: d["risk"] for d in pp.TOOL_DEFS}
    assert risks == {
        "permissions.grant": "high",
        "permissions.revoke": "low",
        "permissions.list": "low",
        "permissions.set_default": "high",
        "permissions.check": "low",
    }
    names = [d["name"] for d in pp.TOOL_DEFS]
    assert names == ["permissions.grant", "permissions.revoke", "permissions.list",
                     "permissions.set_default", "permissions.check"]  # deterministic order


def test_risk_table_additions_match_tool_defs_one_to_one(tmpdb):
    assert set(pp.RISK_TABLE_ADDITIONS) == {d["name"] for d in pp.TOOL_DEFS}
    for d in pp.TOOL_DEFS:
        risk, needs_network = pp.RISK_TABLE_ADDITIONS[d["name"]]
        assert risk == d["risk"]
        assert needs_network == d["needs_network"]


def test_register_wires_tools(tmpdb):
    class FakeReg:
        def __init__(self):
            self.tools = {"pre.existing": "tool"}
            self.registered = []

        def register(self, tool):
            self.registered.append(tool)
            self.tools[tool.name] = tool

    reg = FakeReg()
    pp.register(reg)
    assert {t.name for t in reg.registered} == {d["name"] for d in pp.TOOL_DEFS}
    # The snapshot powers wildcard-coverage warnings: pre-existing names only.
    assert pp._TOOL_NAMES_SNAPSHOT == ["pre.existing"]


def test_wildcard_warning_counts_registered_tools(tmpdb):
    class FakeReg:
        def __init__(self):
            self.tools = {"web.search": 1, "web.fetch": 1, "fs.read": 1}

        def register(self, tool):
            pass

    pp.register(FakeReg())
    res = pp.grant_handler({"tool": "web.*", "level": "allow"})
    assert res["covered_tools"] == 2
