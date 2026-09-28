"""Phase 36 — email triage with drafted replies -> approval queue (offline)."""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import inbox_pack, autopilot_pack


def _msg(**kw):
    d = {"id": "gmail:x1", "channel": "gmail", "sender": "ana@example.com",
         "subject_or_first_line": "Q3 invoice",
         "snippet": "Could you please send the Q3 invoice?", "urgency": 70}
    d.update(kw)
    return d


def test_needs_reply_heuristic():
    assert inbox_pack._needs_reply(_msg()) is True
    assert inbox_pack._needs_reply(
        _msg(snippet="Your weekly newsletter is here", urgency=10)) is False
    assert inbox_pack._needs_reply(
        _msg(snippet="Can you call me?", urgency=5)) is False  # too low


def test_draft_is_marked_and_personalized():
    d = inbox_pack._draft_reply(_msg())
    assert "Hi ana" in d
    assert "Q3 invoice" in d
    assert "[DRAFT" in d


def test_drafts_file_proposals_nothing_sent(tmp_path, monkeypatch):
    os.environ["JARVIS_AUTOPILOT_DB"] = str(tmp_path / "ap.db")
    msgs = [_msg(), _msg(id="gmail:x2", subject_or_first_line="Invoice Q4",
                         snippet="Please confirm you got this", urgency=80),
            _msg(id="gmail:x3", subject_or_first_line="Newsletter",
                 snippet="This week's deals", urgency=10)]
    monkeypatch.setattr(inbox_pack, "_triage_impl",
                        lambda a: {"messages": msgs, "channels": {}})
    r = inbox_pack._triage_replies_impl({"limit": 10})
    assert "error" not in r, r
    assert r["flagged"] == 2 and r["filed"] == 2
    assert all(d["proposal_id"] for d in r["drafts"])
    props = autopilot_pack._list_proposals("pending")
    assert len(props) == 2
    steps = props[0]["steps"]
    assert steps[0]["tool"] == "inbox.reply"
    assert "[DRAFT" in steps[0]["args"]["text"]


def test_registration_contract():
    assert inbox_pack.RISK_TABLE_ADDITIONS["inbox.triage_replies"] == (
        "medium", False)
