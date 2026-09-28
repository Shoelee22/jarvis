"""Phase 25 — Memory consolidation (offline).

Covers: dry_run reports without changing anything; real run marks
episodes consolidated and files lessons; already-consolidated episodes
are skipped; too-young episodes are skipped; batching at >10 episodes;
recall still finds consolidated episodes; bad args rejected; empty
store is clean; registration contract.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack, episodic_pack


def _use_db(tmp_path, name):
    os.environ["JARVIS_EPISODIC_DB"] = str(tmp_path / f"{name}-episodic.db")
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / f"{name}-cog.db")
    brain_pack.bind_agent(None)


LESSON_TEXT = ("We raised prices without warning anyone. Lesson: we should "
               "warn gym owners before any pricing change, next time.")


def _seed(n_old=2, n_young=1):
    for i in range(n_old):
        r = episodic_pack.episode_handler({
            "title": f"old episode {i}",
            "what_happened": LESSON_TEXT,
            "when": "2026-01-05"})
        assert "episode_id" in r, r
    for i in range(n_young):
        r = episodic_pack.episode_handler({
            "title": f"young episode {i}",
            "what_happened": "We had lunch today. Nothing notable happened.",
            "when": "today"})
        assert "episode_id" in r, r


def _consolidated_flags():
    conn = episodic_pack._connect()
    try:
        return [r[0] for r in conn.execute(
            "SELECT consolidated FROM episodes ORDER BY happened_at_ts").fetchall()]
    finally:
        conn.close()


def _lesson_count():
    conn = brain_pack._connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
    finally:
        conn.close()


def test_dry_run_changes_nothing(tmp_path):
    _use_db(tmp_path, "dry")
    _seed()
    r = episodic_pack.consolidate_handler({"older_than_days": 90})
    assert "error" not in r, r
    assert r["dry_run"] is True
    assert r["episodes_found"] == 2
    assert r["batches"][0]["marked"] is False
    assert r["batches"][0]["n_episodes"] == 2
    assert len(r["batches"][0]["lesson_candidates"]) >= 1
    assert _consolidated_flags() == [0, 0, 0]
    assert _lesson_count() == 0


def test_real_run_marks_and_files_lessons(tmp_path):
    _use_db(tmp_path, "real")
    _seed()
    r = episodic_pack.consolidate_handler({"older_than_days": 90,
                                           "dry_run": False})
    assert "error" not in r, r
    assert r["dry_run"] is False
    assert r["batches"][0]["marked"] is True
    assert len(r["batches"][0]["lessons_filed"]) >= 1
    assert _consolidated_flags() == [1, 1, 0]
    assert _lesson_count() >= 1
    assert "Raw episodes kept" in r["note"]


def test_second_run_skips_consolidated(tmp_path):
    _use_db(tmp_path, "skip")
    _seed()
    episodic_pack.consolidate_handler({"older_than_days": 90,
                                       "dry_run": False})
    r2 = episodic_pack.consolidate_handler({"older_than_days": 90,
                                            "dry_run": False})
    assert "error" not in r2, r2
    assert r2["episodes_found"] == 0
    assert r2["batches"] == []


def test_batching_at_more_than_ten(tmp_path):
    _use_db(tmp_path, "batch")
    _seed(n_old=12, n_young=0)
    r = episodic_pack.consolidate_handler({"older_than_days": 90,
                                           "dry_run": False})
    assert "error" not in r, r
    assert r["episodes_found"] == 12
    assert len(r["batches"]) == 2
    assert r["batches"][0]["n_episodes"] == 10
    assert r["batches"][1]["n_episodes"] == 2


def test_recall_still_finds_consolidated(tmp_path):
    _use_db(tmp_path, "recall")
    _seed()
    episodic_pack.consolidate_handler({"older_than_days": 90,
                                       "dry_run": False})
    r = episodic_pack.recall_handler({"query": "pricing change gym"})
    assert "error" not in r, r
    assert len(r["episodes"]) >= 1


def test_empty_store_clean(tmp_path):
    _use_db(tmp_path, "empty")
    r = episodic_pack.consolidate_handler({})
    assert "error" not in r, r
    assert r["episodes_found"] == 0
    assert r["lessons"] == 0


def test_bad_threshold_rejected(tmp_path):
    _use_db(tmp_path, "bad")
    r = episodic_pack.consolidate_handler({"older_than_days": "soon"})
    assert "error" in r


class _FakeReg:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool


def test_registration_contract():
    reg = _FakeReg()
    episodic_pack.register(reg)
    assert "memory.consolidate" in reg.tools
    assert "memory.consolidate" in episodic_pack.RISK_TABLE_ADDITIONS
