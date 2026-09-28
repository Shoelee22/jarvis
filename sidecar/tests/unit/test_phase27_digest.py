"""Phase 27 — Approval digest (offline).

Covers: empty queue; all-low grouping; medium flagged; high and unknown
flagged; unknown risk when nothing is bound; age math; digest markdown
shape; read-only (queue unchanged); proposal ids match queue ids;
registration contract.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import autopilot_pack


def _use_db(tmp_path, name):
    os.environ["JARVIS_AUTOPILOT_DB"] = str(tmp_path / f"{name}.db")
    autopilot_pack.bind_agent(None)


def _propose(goal, tools):
    steps = [{"tool": t, "args": {}, "why": f"do {t}"} for t in tools]
    r = autopilot_pack.propose_handler({"goal": goal, "steps": steps})
    assert r.get("ok"), r
    return r["id"]


class _Tool:
    def __init__(self, risk):
        self.risk = risk


class _Reg:
    def __init__(self, risks):
        self.tools = {n: _Tool(r) for n, r in risks.items()}


class _Agent:
    def __init__(self, reg):
        self.registry = reg


def _bind(risks):
    autopilot_pack.bind_agent(_Agent(_Reg(risks)))


def _digest(**kw):
    r = autopilot_pack.digest_handler(kw)
    assert "error" not in r, r
    return r


def test_empty_queue(tmp_path):
    _use_db(tmp_path, "empty")
    r = _digest()
    assert r["count"] == 0
    assert all(v == [] for v in r["groups"].values())
    assert "0 proposal(s) waiting" in r["digest_md"]


def test_groups_by_risk(tmp_path):
    _use_db(tmp_path, "groups")
    _bind({"t_low": "low", "t_med": "medium", "t_high": "high"})
    _propose("safe job", ["t_low"])
    _propose("review job", ["t_low", "t_med"])
    _propose("careful job", ["t_high"])
    _propose("mystery job", ["t_never_seen"])
    r = _digest()
    assert r["count"] == 4
    assert [i["goal"] for i in r["groups"]["all_low"]] == ["safe job"]
    assert [i["goal"] for i in r["groups"]["has_medium"]] == ["review job"]
    got = [i["goal"] for i in r["groups"]["has_high_or_unknown"]]
    assert got == ["careful job", "mystery job"]
    md = r["digest_md"]
    assert "Safe to batch-approve" in md
    assert "Review first" in md
    assert "Careful review" in md
    assert "safe job" in md and "mystery job" in md


def test_max_risk_and_source_disclosed(tmp_path):
    _use_db(tmp_path, "src")
    _bind({"t_low": "low"})
    _propose("safe job", ["t_low"])
    r = _digest()
    item = r["groups"]["all_low"][0]
    assert item["max_risk"] == "low"
    assert item["risk_from"] == "registry"


def test_unknown_risk_when_unbound(tmp_path):
    _use_db(tmp_path, "unbound")
    autopilot_pack.bind_agent(None)
    _propose("mystery job", ["t_never_seen"])
    r = _digest()
    item = r["groups"]["has_high_or_unknown"][0]
    assert item["max_risk"] == "unknown"
    assert item["risk_from"] == "none"


def test_age_and_tools_listed(tmp_path):
    _use_db(tmp_path, "age")
    _bind({"t_low": "low"})
    _propose("safe job", ["t_low", "t_low"])
    r = _digest()
    item = r["groups"]["all_low"][0]
    assert item["n_steps"] == 2
    assert item["tools"] == ["t_low", "t_low"]
    assert "h old" in item["age"]


def test_read_only(tmp_path):
    _use_db(tmp_path, "ro")
    _bind({"t_low": "low"})
    pid = _propose("safe job", ["t_low"])
    _digest()
    q = autopilot_pack.queue_handler({})
    assert q["count"] == 1
    assert q["pending"][0]["id"] == pid


def test_read_discloses_limits(tmp_path):
    _use_db(tmp_path, "read")
    r = _digest()
    assert "unknown" in r["read"]
    assert "stays human" in r["read"]


class _FakeReg:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool


def test_registration_contract():
    reg = _FakeReg()
    autopilot_pack.register(reg)
    assert "autopilot.digest" in reg.tools
    assert "autopilot.digest" in autopilot_pack.RISK_TABLE_ADDITIONS
