import sys, tempfile
sys.path.insert(0, "sidecar")
from jarvis.trainer.selftune import SelfTuner, Miner, Synthesizer, Promoter
from jarvis.trainer.jobs import JobRunner
from jarvis.trainer.dataset import pii_scan, build_dataset
from jarvis.security import egress

def test_pii_scan_flags_email():
    assert "email" in pii_scan("contact me at raj@example.com")

def test_miner_finds_rephrasing():
    m = Miner()
    entries = [{"tool": "reminders.add", "result_summary": "user rephrased request twice"}] * 2
    pats = m.mine(entries, [])
    assert any("reminders.add" in p["pattern"] for p in pats)

def test_promoter_gates():
    p = Promoter()
    assert p.should_promote(0.0, 0.1)
    assert not p.should_promote(0.06, 0.2)   # regression
    assert not p.should_promote(0.0, 0.0)    # no improvement

def test_selftune_assisted_proposes():
    st = SelfTuner(autonomy="assisted")
    corr = [{"prompt": "remind me weekdays", "bad_response": "one-off", "correction": "weekday 9am"}] * 6
    out = st.cycle([], corr, [])
    assert out.get("needs_approval") and out["proposal"]["pairs"] >= 5

def test_selftune_autonomous_trains_and_reports(tmp_path):
    st = SelfTuner(autonomy="autonomous", runner=JobRunner(tmp_path))
    corr = [{"prompt": f"task {i}", "bad_response": "bad", "correction": "good"} for i in range(6)]
    out = st.cycle([], corr, [])
    assert out["ran"] and out["promoted"] and "Retrained" in out["report"]

def test_rollback():
    st = SelfTuner()
    st.checkpoints = {"active": "adapter-2", "history": ["adapter-1", "adapter-2"]}
    assert st.rollback()["active"] == "adapter-1"

def test_egress_default_deny():
    assert not egress.check("mail.send", "evil.com")
    assert egress.violations()
