"""Unit tests for the Phase 8 PC Management Tool Pack. Fully offline:
no subprocess/winget calls ever run (winget_* verified on Linux where
they must return honest error dicts), and duplicates / big_files /
disk_report / startup_list run for real in tmp dirs.
"""
import sys
from pathlib import Path

sys.path.insert(0, "sidecar")

import pytest  # noqa: E402

from jarvis.tools.builtin import pc_pack  # noqa: E402


# ---------------------------------------------------------------- fixtures
@pytest.fixture()
def dup_tree(tmp_path):
    """Two identical 200 KB blobs, one unique file, one tiny duplicate."""
    blob = b"x" * (200 * 1024)
    (tmp_path / "a.bin").write_bytes(blob)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(blob)
    (tmp_path / "unique.bin").write_bytes(b"y" * (200 * 1024))
    (tmp_path / "tiny1.txt").write_text("same-tiny")
    (tmp_path / "tiny2.txt").write_text("same-tiny")
    return tmp_path


# ------------------------------------------------------- pc.duplicates
def test_duplicates_finds_sha256_group(dup_tree):
    r = pc_pack.duplicates({"dir": str(dup_tree), "min_size_kb": 100})
    assert "error" not in r, r
    assert r["files_scanned"] >= 5
    assert r["group_count"] == 1
    g = r["duplicate_groups"][0]
    assert len(g["paths"]) == 2
    assert g["size_bytes"] == 200 * 1024
    assert g["wasted_bytes"] == 200 * 1024
    assert r["wasted_bytes"] == 200 * 1024
    assert "same-tiny" not in "\n".join(g["paths"])  # min_size_kb filtered it


def test_duplicates_zero_min_size_includes_tiny(dup_tree):
    r = pc_pack.duplicates({"dir": str(dup_tree), "min_size_kb": 0})
    assert "error" not in r, r
    assert r["group_count"] == 2  # blob pair + tiny pair


def test_duplicates_missing_dir():
    r = pc_pack.duplicates({"dir": "/nope/not/here", "min_size_kb": 0})
    assert "error" in r


def test_duplicates_refuses_root():
    r = pc_pack.duplicates({"dir": "/", "min_size_kb": 0})
    assert "error" in r and "refusing" in r["error"]


# -------------------------------------------------------- pc.big_files
def test_big_files_sorted_desc(dup_tree):
    r = pc_pack.big_files({"dir": str(dup_tree), "n": 2})
    assert "error" not in r, r
    assert len(r["top"]) == 2
    sizes = [t["size_bytes"] for t in r["top"]]
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[0] == 200 * 1024
    assert all(t["size_mb"] == round(t["size_bytes"] / 1048576, 2) for t in r["top"])


def test_big_files_clamps_n(dup_tree):
    r = pc_pack.big_files({"dir": str(dup_tree), "n": 9999})
    assert r["n"] == 200
    assert "error" not in r


# ------------------------------------------------------ pc.disk_report
def test_disk_report_numbers():
    r = pc_pack.disk_report({})
    assert "error" not in r, r
    assert isinstance(r["disks"], list) and r["disks"]
    for d in r["disks"]:
        if "error" in d:
            continue
        assert d["total_gb"] > 0
        assert 0 <= d["used_gb"] <= d["total_gb"] + 0.05
        assert d["free_gb"] >= 0
        assert 0.0 <= d["used_pct"] <= 100.0
        # used_pct must be consistent with the reported GB figures.
        # On tiny mounts the 2-decimal GB rounding dominates the ratio, so
        # scale the tolerance with the rounding error instead of failing.
        expect_pct = round(d["used_gb"] / d["total_gb"] * 100, 1)
        tol = max(0.5, (0.01 / d["total_gb"]) * 100)
        assert abs(d["used_pct"] - expect_pct) < tol


# ----------------------------------------------------- pc.startup_list
def test_startup_list_no_crash():
    r = pc_pack.startup_list({})
    assert isinstance(r, dict)
    if "error" in r:
        # unsupported platform: must be an honest error, not a raise
        assert isinstance(r["error"], str)
    else:
        assert r["count"] == len(r["entries"])
        assert r["os"] == pc_pack.OSNAME


# ---------------------------------------------------------- winget_*
def test_winget_search_linux_honest_error(monkeypatch):
    monkeypatch.setattr(pc_pack, "OSNAME", "Linux")
    r = pc_pack.winget_search({"query": "vscode"})
    assert r == {"error": "winget is Windows-only"}


def test_winget_search_requires_query(monkeypatch):
    monkeypatch.setattr(pc_pack, "OSNAME", "Windows")
    r = pc_pack.winget_search({"query": "  "})
    assert r == {"error": "query is required"}


def test_winget_list_linux_honest_error(monkeypatch):
    monkeypatch.setattr(pc_pack, "OSNAME", "Linux")
    r = pc_pack.winget_list({})
    assert r == {"error": "winget is Windows-only"}


def test_winget_install_linux_honest_error(monkeypatch):
    monkeypatch.setattr(pc_pack, "OSNAME", "Linux")
    r = pc_pack.winget_install({"package_id": "Microsoft.VSCode"})
    assert r == {"error": "winget is Windows-only"}


def test_winget_install_rejects_injection(monkeypatch):
    monkeypatch.setattr(pc_pack, "OSNAME", "Windows")
    r = pc_pack.winget_install({"package_id": "foo; rm -rf"})
    assert "error" in r and "invalid package_id" in r["error"]


def test_winget_install_rejects_path_and_flags(monkeypatch):
    monkeypatch.setattr(pc_pack, "OSNAME", "Windows")
    for bad in ("../../etc", "pkg --foo", "a b", "pkg|whoami", "pkg$(id)"):
        r = pc_pack.winget_install({"package_id": bad})
        assert "invalid package_id" in r["error"], bad


def test_winget_install_accepts_valid_id_format(monkeypatch):
    # Regex is the only gate before PATH lookup; missing winget binary must
    # still return an honest error, never attempt a real install.
    monkeypatch.setattr(pc_pack, "OSNAME", "Windows")
    monkeypatch.setattr(pc_pack.shutil, "which", lambda _: None)
    r = pc_pack.winget_install({"package_id": "Microsoft.VSCode"})
    assert r == {"error": "winget not found on PATH"}


# ----------------------------------------------------------- TOOL_DEFS
def test_tool_defs_count_and_shape():
    assert len(pc_pack.TOOL_DEFS) == 7
    names = [t["name"] for t in pc_pack.TOOL_DEFS]
    assert names == ["pc.winget_search", "pc.winget_install", "pc.winget_list",
                     "pc.duplicates", "pc.big_files", "pc.disk_report",
                     "pc.startup_list"]
    for t in pc_pack.TOOL_DEFS:
        assert callable(t["handler"])
        assert t["risk"] in {"low", "medium", "high"}
        assert isinstance(t["needs_network"], bool)
        assert isinstance(t["schema"], dict)
        assert isinstance(t["description"], str) and t["description"]


def test_tool_defs_risks():
    risks = {t["name"]: t["risk"] for t in pc_pack.TOOL_DEFS}
    assert risks["pc.winget_install"] == "high"
    for name in ("pc.winget_search", "pc.winget_list", "pc.duplicates",
                 "pc.big_files", "pc.disk_report", "pc.startup_list"):
        assert risks[name] == "low"


def test_tool_defs_network_flags():
    net = {t["name"]: t["needs_network"] for t in pc_pack.TOOL_DEFS}
    assert net["pc.winget_search"] is True
    assert net["pc.winget_install"] is True
    assert net["pc.winget_list"] is False
    assert net["pc.duplicates"] is False
