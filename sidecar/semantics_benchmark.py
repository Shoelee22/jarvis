#!/usr/bin/env python3
"""Phase 21 benchmark: keyword overlap vs real embedding similarity.

Run on a machine with the semantic backend installed:
    pip install sentence-transformers torch
    # then enable in tools_config.yaml: semantics.enabled: true
    python3 semantics_benchmark.py

For each pair in tests/unit/fixtures/semantic_benchmark.json it computes:
  - keyword score: Jaccard overlap of token sets (the old brain scoring)
  - semantic score: cosine similarity of embeddings (the new brain scoring)

and reports per-kind accuracy of a naive threshold classifier
(sim >= 0.75 means "same meaning"). The hypothesis: semantic beats
keyword on paraphrases, keyword traps are caught by neither being
perfect, and negation traps are a KNOWN FAILURE for cosine
(disclosed in the tool docs — negation still scores high).

This script never touches the registry; it uses semantics_pack.get_engine()
directly, which is the same backend the brain tools use.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent / "tests" / "unit" / "fixtures" / "semantic_benchmark.json"

_STOP = frozenset("the a an of to in on is are was were be been and or for with at by from as it this that these those i you he she we they my your his her our their not no do does did".split())


def _tokens(s: str) -> set:
    return {t for t in "".join(c.lower() if c.isalnum() else " " for c in s).split()
            if t and t not in _STOP}


def keyword_score(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def main() -> int:
    sys.path.insert(0, str(Path(__file__).parent / "sidecar"))
    from jarvis.tools.builtin import semantics_pack

    eng, err = semantics_pack.get_engine()
    if eng is None:
        print(f"backend unavailable: {err}")
        print("Install sentence-transformers + torch and set semantics.enabled: true")
        return 2
    print(f"backend: {eng.model_name}\n")

    pairs = json.loads(FIXTURE.read_text())
    kinds: dict[str, dict] = {}
    negation_fails = 0
    for p in pairs:
        a, b = p["a"], p["b"]
        ks = keyword_score(a, b)
        ss = max(0.0, eng.similarity(a, b))
        same_kw = ks >= 0.30
        same_sem = ss >= 0.75
        truth = p["kind"] == "paraphrase"
        ok_kw = same_kw == truth
        ok_sem = same_sem == truth
        k = kinds.setdefault(p["kind"], {"n": 0, "kw": 0, "sem": 0})
        k["n"] += 1
        k["kw"] += ok_kw
        k["sem"] += ok_sem
        flag = ""
        if p["kind"] == "negation_trap" and same_sem:
            negation_fails += 1
            flag = "  <-- known failure: negation still similar"
        print(f"[{p['kind']:>14}] kw={ks:.2f} sem={ss:.2f} "
              f"kw_ok={ok_kw} sem_ok={ok_sem}{flag}")

    print("\nPer-kind accuracy (threshold classifier, same-meaning?):")
    for kind, k in kinds.items():
        print(f"  {kind:>14}: keyword {k['kw']}/{k['n']}  semantic {k['sem']}/{k['n']}")
    tot_kw = sum(k["kw"] for k in kinds.values())
    tot_sem = sum(k["sem"] for k in kinds.values())
    n = len(pairs)
    print(f"\nTOTAL: keyword {tot_kw}/{n} ({tot_kw/n:.0%})  "
          f"semantic {tot_sem}/{n} ({tot_sem/n:.0%})")
    print(f"negation traps fooled by cosine: {negation_fails}/4 (disclosed limitation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
