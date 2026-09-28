"""Phase 34 — topic consolidation (offline).

Covers: by_topic groups related episodes into fewer batches than
chronological; unrelated episodes split across batches; chronological
remains the default; dry-run reports topic batches without marking;
real run files per-topic lessons and marks episodes; registration
contract (schema now includes by_topic).
"""
import os
import sys
import time

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import episodic_pack, brain_pack, semantics_pack


def _fake_embed(texts):
    vecs = []
    for t in texts:
        tl = str(t).lower()
        if "gym" in tl or "pricing" in tl or "prices" in tl:
            vecs.append([1.0, 0.0, 0.0])
        elif "dent" in tl:
            vecs.append([0.0, 1.0, 0.0])
        else:
            vecs.append([0.0, 0.0, 1.0])
    return vecs


def _use_db(tmp_path, fake_backend=True):
    os.environ["JARVIS_EPISODIC_DB"] = str(tmp_path / "ep.db")
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "b.db")
    brain_pack.bind_agent(None)
    semantics_pack.clear_backend_override()
    semantics_pack._backend = None
    semantics_pack._backend_error = None
    if fake_backend:
        semantics_pack.set_backend_override(_fake_embed)


def _old_ts(days=100):
    return time.time() - days * 86400


def _seed():
    eps = [
        ("Gym pricing meeting", "Discussed raising gym membership prices with the team", "decided to test"),
        ("Price test results", "Raised gym prices for a trial group and measured churn", "churn stayed flat"),
        ("Membership pricing review", "Reviewed gym membership pricing against competitors", "we are mid-range"),
        ("Dentist appointment", "Went to the dentist for a cleaning", "teeth fine"),
        ("Car service", "Took the car in for its annual service", "all good"),
        ("Dental checkup followup", "Followup visit to the dentist", "no cavities"),
    ]
    for title, what, outcome in eps:
        r = episodic_pack.episode_handler(
            {"title": title, "what_happened": what, "outcome": outcome})
        assert "error" not in r, r
    # age them all past the 90-day default
    with episodic_pack._LOCK:
        conn = episodic_pack._connect()
        try:
            conn.execute("UPDATE episodes SET happened_at_ts=?",
                         (_old_ts(),))
            conn.commit()
        finally:
            conn.close()


def test_topic_batches_group_related(tmp_path):
    _use_db(tmp_path)
    _seed()
    chrono = episodic_pack.consolidate_handler({"dry_run": True})
    topic = episodic_pack.consolidate_handler(
        {"dry_run": True, "by_topic": True})
    assert "by_topic" in topic["method"], topic["method"]
    assert "chronological" in chrono["method"]
    assert len(topic["batches"]) >= 1
    # the three gym/pricing episodes share one batch
    with episodic_pack._LOCK:
        conn = episodic_pack._connect()
        try:
            rows = conn.execute("SELECT id, title FROM episodes").fetchall()
        finally:
            conn.close()
    gym_ids = {r[0] for r in rows if "Gym" in r[1] or "Price" in r[1]
               or "pricing" in r[1]}
    assert len(gym_ids) == 3
    gym_batches = [b for b in topic["batches"]
                   if gym_ids <= set(b["episodes"])]
    assert len(gym_batches) == 1, [b["episodes"] for b in topic["batches"]]


def test_unrelated_split(tmp_path):
    _use_db(tmp_path)
    _seed()
    topic = episodic_pack.consolidate_handler(
        {"dry_run": True, "by_topic": True})

    def batch_of(ep_id):
        for b in topic["batches"]:
            if ep_id in b["episodes"]:
                return b
        return None

    with episodic_pack._LOCK:
        conn = episodic_pack._connect()
        try:
            rows = conn.execute("SELECT id, title FROM episodes").fetchall()
        finally:
            conn.close()
    gym_ids = [r[0] for r in rows if "Gym" in r[1] or "Price" in r[1]
               or "pricing" in r[1]]
    dent_ids = [r[0] for r in rows if "Dent" in r[1]]
    gym_batches = {id(batch_of(i)) for i in gym_ids}
    dent_batches = {id(batch_of(i)) for i in dent_ids}
    assert gym_batches.isdisjoint(dent_batches), (gym_batches, dent_batches)


def test_real_run_marks_and_files(tmp_path):
    _use_db(tmp_path)
    _seed()
    r = episodic_pack.consolidate_handler(
        {"dry_run": False, "by_topic": True})
    assert r["dry_run"] is False
    assert r["episodes_found"] == 6
    with episodic_pack._LOCK:
        conn = episodic_pack._connect()
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM episodes WHERE consolidated=1"
            ).fetchone()[0]
        finally:
            conn.close()
    assert n == 6


def test_schema_has_by_topic():
    spec = next(s for s in episodic_pack.TOOL_DEFS
                if s["name"] == "memory.consolidate")
    assert "by_topic?" in spec["schema"]


def test_no_backend_falls_back_honestly(tmp_path):
    _use_db(tmp_path, fake_backend=False)
    _seed()
    r = episodic_pack.consolidate_handler(
        {"dry_run": True, "by_topic": True})
    assert "topic clustering failed" in r["method"], r["method"]
    # still covers every episode exactly once
    seen = [e for b in r["batches"] for e in b["episodes"]]
    assert len(seen) == 6 and len(set(seen)) == 6
