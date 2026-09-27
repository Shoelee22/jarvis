import sys, tempfile
sys.path.insert(0, "sidecar")
from jarvis.memory.store import MemoryStore
from jarvis.memory.consolidation import consolidate

def test_remember_search_forget():
    ms = MemoryStore(":memory:")
    ms.remember("fact", "my gym opens at 6am", source="test")
    hits = ms.search("gym hours")
    assert any("gym opens at 6am" in h["text"] for h in hits)
    assert ms.forget("gym hours") >= 1
    assert not any("gym opens" in h["text"] for h in ms.search("gym"))
    ms.close()

def test_correction_logged_as_training():
    ms = MemoryStore(":memory:")
    ms.log_correction("remind me weekdays", "one-off reminder", "weekday 9am reminder")
    ms.close()

def test_consolidation_dedupes(tmp_path):
    db = str(tmp_path / "b.db")
    ms = MemoryStore(db)
    ms.remember("fact", "i like coffee", source="test")
    ms.remember("fact", "i like coffee", source="test")
    log = consolidate(db)
    assert log["merged"] >= 1 and log["kept"] >= 1
    ms.close()
