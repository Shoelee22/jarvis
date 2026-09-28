"""Offline tests for the Phase 7 dynamic tool providers (Worker 8).

Covers every dynamic namespace directly against the landed modules:
convert (units), automation, joke, opinion, knowledge (sqlite-FTS5), contact,
app, web, greet/say (languages), plus the DynamicRegistry end to end.

No network, no real credentials, no real user data: every provider is
constructed with a tmp data_dir, fake registries, and monkeypatched stores.
"""
from __future__ import annotations

import re
import sqlite3

import pytest

from jarvis.tools.dynamic import build_dynamic_registry
from jarvis.tools.dynamic.units import UnitsProvider, _ALL_UNITS
from jarvis.tools.dynamic.automation import AutomationEngine, AutomationProvider
from jarvis.tools.dynamic.apps import AppsProvider
from jarvis.tools.dynamic import web_actions as web_actions_mod
from jarvis.tools.dynamic.web_actions import PAIR_COUNT, WebActionsProvider
from jarvis.tools.dynamic.knowledge_wrap import KnowledgeProvider


# ===========================================================================
# units: convert.<a>_to_<b>, N*(N-1) addressable names
# ===========================================================================

class TestUnits:
    def test_unit_count_at_least_400(self):
        assert len(_ALL_UNITS) >= 400

    def test_expand_is_n_times_n_minus_1(self):
        n = len(_ALL_UNITS)
        assert UnitsProvider().expand() == n * (n - 1) >= 159600

    def test_kilometer_to_mile(self):
        tool = UnitsProvider().resolve("convert.kilometer_to_mile")
        assert tool is not None
        out = tool.handler({"value": 1})
        assert out["result"] == pytest.approx(0.621371)

    def test_celsius_to_fahrenheit(self):
        tool = UnitsProvider().resolve("convert.degrees_celsius_to_degrees_fahrenheit")
        assert tool is not None
        out = tool.handler({"value": 0})
        assert out["result"] == 32

    def test_cross_dimension_is_addressable_but_errors(self):
        tool = UnitsProvider().resolve("convert.kilometer_to_kilogram")
        assert tool is not None  # counted, but incompatible
        out = tool.handler({"value": 1})
        assert "error" in out and "dimension" in out["error"].lower()

    def test_unknown_pair_resolves_none(self):
        assert UnitsProvider().resolve("convert.meter_to_blorpt") is None


# ===========================================================================
# automation: create -> run -> delete roundtrip against a FakeRegistry
# ===========================================================================

class FakeRegistry:
    """Stand-in for the main registry: records calls, returns {"ok": True}."""

    def __init__(self, fail_on=frozenset()):
        self.fail_on = fail_on
        self.calls: list[tuple[str, dict]] = []

    def call(self, name, args=None, **kwargs):
        self.calls.append((name, args or {}))
        if name in self.fail_on:
            return {"ok": False, "error": "boom"}
        return {"ok": True, "result": {"done": name}}


def _steps(*tools):
    return [{"tool": t, "args": {"n": i}} for i, t in enumerate(tools)]


class TestAutomation:
    def _engine(self, tmp_path, fail_on=frozenset()):
        return AutomationEngine(data_dir=tmp_path,
                                main_registry=FakeRegistry(fail_on))

    def test_create_run_delete_roundtrip(self, tmp_path):
        eng = self._engine(tmp_path)
        created = eng.create("morning", steps=_steps("a", "b"))
        assert created.get("created") == "morning"
        assert created.get("steps") == 2

        run = eng.run("morning")
        assert run.get("completed") == 2
        assert [r["tool"] for r in run["results"]] == ["a", "b"]
        assert all(r["ok"] for r in run["results"])

        assert eng.main_registry.calls == [("a", {"n": 0}), ("b", {"n": 1})]
        assert eng.delete("morning") is True
        assert eng.get("morning") is None
        assert eng.delete("morning") is False

    def test_run_stops_on_failing_step(self, tmp_path):
        eng = self._engine(tmp_path, fail_on=frozenset({"bad"}))
        eng.create("rt", steps=_steps("good", "bad", "never"))
        run = eng.run("rt")
        assert run["ok"] is False
        assert run["stopped_at"] == 1
        assert run["reason"] == "step failed"
        # third step never executed
        assert [r["tool"] for r in run["results"]] == ["good", "bad"]

    def test_provider_static_tools_resolve(self, tmp_path):
        provider = AutomationProvider(data_dir=tmp_path)
        assert provider.expand() >= 5
        assert provider.resolve("automation.create") is not None
        assert provider.resolve("automation.run") is not None
        assert provider.resolve("automation.run.no_such_routine") is None

    def test_provider_create_run_delete_roundtrip(self, tmp_path):
        """Provider-resolved tools honor data_dir + the injected registry."""
        fake = FakeRegistry()
        provider = AutomationProvider(data_dir=str(tmp_path),
                                      main_registry=fake)
        created = provider.resolve("automation.create").handler({
            "name": "morning",
            "steps": [{"tool": "a", "args": {}},
                      {"tool": "b", "args": {}}],
        })
        assert created.get("created") == "morning"
        assert provider.expand() == 6  # 5 static + 1 routine shortcut
        assert provider.resolve("automation.run.morning") is not None

        out = provider.resolve("automation.run").handler({"name": "morning"})
        assert "error" not in out
        assert out["completed"] == 2
        assert [c[0] for c in fake.calls] == ["a", "b"]

        deleted = provider.resolve("automation.delete").handler(
            {"name": "morning"})
        assert deleted.get("deleted") == "morning"
        assert provider.resolve("automation.list").handler({})["routines"] == []

    def test_provider_run_stops_on_failing_step(self, tmp_path):
        fake = FakeRegistry(fail_on=frozenset({"bad"}))
        provider = AutomationProvider(data_dir=str(tmp_path),
                                      main_registry=fake)
        provider.resolve("automation.create").handler({
            "name": "fragile",
            "steps": [{"tool": "good", "args": {}},
                      {"tool": "bad", "args": {}},
                      {"tool": "never", "args": {}}],
        })
        out = provider.resolve("automation.run").handler({"name": "fragile"})
        assert out.get("ok") is False
        assert out.get("stopped_at") == 1
        assert [c[0] for c in fake.calls] == ["good", "bad"]

    def test_provider_cron_routine_stored(self, tmp_path):
        provider = AutomationProvider(data_dir=str(tmp_path),
                                      main_registry=FakeRegistry())
        created = provider.resolve("automation.create").handler({
            "name": "daily",
            "trigger": "cron",
            "cron": "0 9 * * *",
            "steps": [{"tool": "a", "args": {}}],
        })
        assert created.get("created") == "daily"
        routines = provider.resolve("automation.list").handler({})["routines"]
        match = [r for r in routines if r["name"] == "daily"]
        assert len(match) == 1
        assert match[0]["cron"] == "0 9 * * *"
        assert match[0]["trigger"] == "cron"


# ===========================================================================
# jokes
# ===========================================================================

def _bank_of(mod):
    """The jokes module's bank: a dict category->list, or the landed list shape."""
    for attr in ("_BY_CATEGORY", "JOKES_BY_CATEGORY", "BANK"):
        bank = getattr(mod, attr, None)
        if isinstance(bank, dict) and bank:
            return dict(bank)
    jokes = getattr(mod, "JOKES", None)
    if isinstance(jokes, list) and jokes and isinstance(jokes[0], dict):
        grouped: dict = {}
        for j in jokes:
            grouped.setdefault(j.get("category", "misc"), []).append(j)
        return grouped
    raise AssertionError(
        "jokes module exposes no dict bank (_BY_CATEGORY/JOKES list)")


class TestJokes:
    def test_bank_size_and_categories(self):
        jokes = pytest.importorskip("jarvis.tools.dynamic.jokes")
        bank = _bank_of(jokes)
        total = sum(len(v) for v in bank.values())
        assert total >= 250
        assert len(bank) >= 10
        assert all(len(v) >= 25 for v in bank.values())

    def test_random_returns_text(self, tmp_path):
        jokes = pytest.importorskip("jarvis.tools.dynamic.jokes")
        provider = jokes.JokesProvider(data_dir=tmp_path)
        assert provider.namespace == "joke"
        tool = provider.resolve("joke.random")
        assert tool is not None
        out = tool.handler({})
        assert isinstance(out, dict)
        assert out.get("joke") or out.get("text")

    def test_expand_matches_math(self, tmp_path):
        jokes = pytest.importorskip("jarvis.tools.dynamic.jokes")
        provider = jokes.JokesProvider(data_dir=tmp_path)
        bank = _bank_of(jokes)
        assert provider.expand() == 1 + len(bank) + sum(len(v) for v in bank.values())


# ===========================================================================
# languages
# ===========================================================================

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


class TestLanguages:
    def test_greet_expand_and_hindi(self, tmp_path):
        langs = pytest.importorskip("jarvis.tools.dynamic.languages")
        provider = langs.GreetProvider(data_dir=tmp_path)
        assert provider.namespace == "greet"
        assert provider.expand() == 12
        tool = provider.resolve("greet.hi")
        assert tool is not None
        out = tool.handler({})
        text = out if isinstance(out, str) else out.get("text") or out.get("greeting")
        assert isinstance(text, str) and _DEVANAGARI.search(text), \
            f"greet.hi should return Devanagari, got: {text!r}"

    def test_say_without_voice_errors(self, tmp_path):
        langs = pytest.importorskip("jarvis.tools.dynamic.languages")
        provider = langs.SayProvider(data_dir=tmp_path)
        assert provider.namespace == "say"
        assert provider.expand() == 12
        tool = provider.resolve("say.hi")
        assert tool is not None
        out = tool.handler({"text": "hello"})
        assert "error" in out
        assert "voice not installed" in out["error"].lower()


# ===========================================================================
# opinions: opinion.get {topic} -> stance (None when unknown)
# ===========================================================================

class TestOpinions:
    def _provider(self):
        opinions = pytest.importorskip("jarvis.tools.dynamic.opinions")
        provider = opinions.OpinionProvider()
        assert provider.namespace == "opinion"
        assert provider.expand() == 1
        tool = provider.resolve("opinion.get")
        assert tool is not None
        return opinions, tool

    def test_known_topic_has_stance(self):
        opinions, tool = self._provider()
        known = next(iter(opinions.OPINIONS))
        out = tool.handler({"topic": known})
        assert out["stance"] is not None
        assert out["topic"] == known

    def test_unknown_topic_stance_none(self):
        _, tool = self._provider()
        out = tool.handler({"topic": "topic_that_does_not_exist_xyz"})
        assert out["stance"] is None

    def test_missing_topic_errors(self):
        _, tool = self._provider()
        assert "error" in tool.handler({})


# ===========================================================================
# knowledge: sqlite-FTS5 add -> ask roundtrip
# ===========================================================================

class TestKnowledge:
    def _provider(self, tmp_path):
        provider = KnowledgeProvider(data_dir=tmp_path)
        assert provider.namespace == "knowledge"
        assert provider.expand() == 2
        assert provider.sample_names() == ["knowledge.add_text", "knowledge.ask"]
        return provider

    def test_add_then_ask_roundtrip(self, tmp_path):
        provider = self._provider(tmp_path)
        add = provider.resolve("knowledge.add_text")
        ask = provider.resolve("knowledge.ask")
        assert add is not None and ask is not None

        added = add.handler({"text": "The pet xylophone lives in the garage.",
                             "source": "test"})
        assert added.get("id"), added
        assert added.get("chars") == len("The pet xylophone lives in the garage.")

        out = ask.handler({"query": "xylophone"})
        assert out.get("results"), out
        first = out["results"][0]
        assert "xylophone" in first["snippet"]  # <b>-marked match term
        assert first["source"] == "test"

    def test_ask_no_match_is_empty(self, tmp_path):
        provider = self._provider(tmp_path)
        provider.resolve("knowledge.add_text").handler({"text": "nothing here"})
        out = provider.resolve("knowledge.ask").handler({"query": "zzzqqq"})
        assert out["results"] == []

    def test_odd_queries_do_not_crash(self, tmp_path):
        ask = self._provider(tmp_path).resolve("knowledge.ask").handler
        assert ask({"query": "))((**::^^"}) == {"results": []}
        assert ask({"query": ""}) == {"results": []}
        assert ask({"query": "AND OR NOT"}) == {"results": []}

    def test_add_empty_text_errors(self, tmp_path):
        provider = self._provider(tmp_path)
        out = provider.resolve("knowledge.add_text").handler({"text": "   "})
        assert "error" in out

    def test_unknown_name_resolves_none(self, tmp_path):
        assert self._provider(tmp_path).resolve("knowledge.bogus") is None


# ===========================================================================
# contacts: C x 5 channels x 30 templates, monkeypatched sqlite store
# ===========================================================================

def _temp_contacts_db(tmp_path):
    db = tmp_path / "contacts.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, phone TEXT, email TEXT, notes TEXT)")
    conn.execute(
        "INSERT INTO contacts (name, phone, email, notes) VALUES (?,?,?,?)",
        ("test_user", "555-1234", "test@example.com", "notes"))
    conn.commit()
    conn.close()
    return db


class TestContacts:
    def test_expand_and_sms_risk(self, tmp_path, monkeypatch):
        mod = pytest.importorskip("jarvis.tools.dynamic.contacts_actions")
        db = _temp_contacts_db(tmp_path)
        monkeypatch.setattr(mod, "_contacts_conn",
                            lambda: sqlite3.connect(str(db)))
        provider = mod.ContactsActionsProvider()
        assert provider.namespace == "contact"
        assert provider.expand() == 1 * 5 * 30
        tool = provider.resolve("contact.sms.test_user.birthday")
        assert tool is not None
        assert tool.risk == "high"

    def test_unknown_slug_or_template(self, tmp_path, monkeypatch):
        mod = pytest.importorskip("jarvis.tools.dynamic.contacts_actions")
        db = _temp_contacts_db(tmp_path)
        monkeypatch.setattr(mod, "_contacts_conn",
                            lambda: sqlite3.connect(str(db)))
        provider = mod.ContactsActionsProvider()
        assert provider.resolve("contact.sms.nobody.birthday") is None
        assert provider.resolve("contact.sms.test_user.nope") is None
        assert provider.resolve("contact.definitely_not_a_real_tool_xyz") is None

    def test_no_db_zero_expand(self, tmp_path, monkeypatch):
        mod = pytest.importorskip("jarvis.tools.dynamic.contacts_actions")

        def _boom():
            raise sqlite3.OperationalError("no such table")

        monkeypatch.setattr(mod, "_contacts_conn", _boom)
        assert mod.ContactsActionsProvider().expand() == 0


# ===========================================================================
# apps: expand doesn't crash; web: PAIR_COUNT and youtube handler (offline)
# ===========================================================================

class TestApps:
    def test_expand_does_not_crash(self, tmp_path):
        n = AppsProvider(data_dir=tmp_path).expand()
        assert isinstance(n, int) and n >= 0


class TestWebActions:
    def test_pair_count(self):
        assert PAIR_COUNT >= 300
        assert WebActionsProvider().expand() == PAIR_COUNT

    def test_youtube_search_url_offline(self, monkeypatch):
        # Fully offline: never attempt the <title> fetch.
        monkeypatch.setattr(
            web_actions_mod, "section",
            lambda name, default=None: {"fetch_snippets": False})
        tool = WebActionsProvider().resolve("web.youtube.search")
        assert tool is not None
        out = tool.handler({"q": "cats"})
        assert "url" in out and "cats" in out["url"]

    def test_unknown_site_resolves_none(self):
        assert WebActionsProvider().resolve("web.nope.search") is None


# ===========================================================================
# registry: end-to-end over all landed providers
# ===========================================================================

class TestDynamicRegistry:
    def test_count_and_namespaces(self, tmp_path):
        reg = build_dynamic_registry(None, tmp_path)
        total = reg.count_addressable()
        assert total >= 150000, f"expected >=150000, got {total}"
        ns = reg.list_namespaces()
        assert ns["convert"] >= 159600
        assert ns["web"] >= 300
        assert ns["knowledge"] == 2

    def test_resolve_convert_pair(self, tmp_path):
        reg = build_dynamic_registry(None, tmp_path)
        tool = reg.resolve("convert.kilometer_to_mile")
        assert tool is not None
        assert tool.name == "convert.kilometer_to_mile"

    def test_call_convert_pair(self, tmp_path):
        reg = build_dynamic_registry(None, tmp_path)
        out = reg.call("convert.kilometer_to_mile", {"value": 2})
        assert out["ok"] is True
        assert out["result"]["result"] == pytest.approx(1.242742384474)

    def test_call_unknown_name_ok_false(self, tmp_path):
        reg = build_dynamic_registry(None, tmp_path)
        assert reg.resolve("does.not.exist") is None
        out = reg.call("does.not.exist")
        assert out["ok"] is False

    def test_call_knowledge_roundtrip(self, tmp_path):
        reg = build_dynamic_registry(None, tmp_path)
        assert reg.call("knowledge.ask",
                        {"query": "regtest_zzz"})["ok"] is True
