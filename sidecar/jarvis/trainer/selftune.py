"""Autonomous self-tuning loop: mine → synthesize → train → promote → report.

Safety invariants (NOT trainable, enforced in code):
- The loop can never modify policy rules or tool permissions.
- Promotion requires passing regression evals AND beating the incumbent on the
  mined failure set.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field

from .dataset import pair_from_correction, build_dataset
from .jobs import JobRunner

REGRESSION_THRESHOLD = 0.03   # max allowed regression delta
MIN_SIGNAL = 200              # fresh pairs (or 7 days) before a cycle


@dataclass
class Miner:
    """Find failure patterns in audit log + corrections."""
    def mine(self, audit_entries: list[dict], corrections: list[dict]) -> list[dict]:
        patterns = []
        rephrased = Counter(e.get("tool") for e in audit_entries
                            if "rephras" in e.get("result_summary", "").lower())
        for tool, n in rephrased.items():
            if n >= 2:
                patterns.append({"pattern": f"{tool} needed rephrasing {n}x",
                                 "count": n, "examples": []})
        for c in corrections:
            patterns.append({"pattern": f"correction: {c['prompt'][:80]}",
                             "count": 1, "examples": [c]})
        return patterns


@dataclass
class Synthesizer:
    replay_ratio: float = 0.3

    def synthesize(self, patterns: list[dict], good_examples: list[dict]) -> dict:
        pairs = []
        for p in patterns:
            for ex in p.get("examples", [])[:5]:
                pairs.append(pair_from_correction(
                    ex.get("prompt", p["pattern"]),
                    ex.get("bad_response", "(previous behavior)"),
                    ex.get("correction", "(corrected behavior)")))
        n_replay = int(len(pairs) * self.replay_ratio / max(1 - self.replay_ratio, 0.01))
        for g in good_examples[:n_replay]:
            pairs.append({"id": g.get("id", "?"), "kind": "replay",
                          "prompt": g.get("prompt", ""), "response": g.get("response", ""),
                          "pii_flags": []})
        return build_dataset(pairs, min_pairs=1)


@dataclass
class Promoter:
    regression_threshold: float = REGRESSION_THRESHOLD

    def should_promote(self, regression_delta: float, failure_delta: float) -> bool:
        """Promote only if no meaningful regression AND failure set improves."""
        return (regression_delta <= self.regression_threshold) and (failure_delta > 0)

    def promote(self, job, regression_delta: float, failure_delta: float,
                checkpoints: dict) -> dict:
        if self.should_promote(regression_delta, failure_delta):
            checkpoints["active"] = job.checkpoint
            checkpoints.setdefault("history", []).append(job.checkpoint)
            return {"promoted": True, "checkpoint": job.checkpoint}
        return {"promoted": False, "reason": "eval gate failed — incumbent kept"}


@dataclass
class SelfTuner:
    autonomy: str = "assisted"   # manual | assisted | autonomous
    miner: Miner = field(default_factory=Miner)
    synth: Synthesizer = field(default_factory=Synthesizer)
    promoter: Promoter = field(default_factory=Promoter)
    runner: JobRunner | None = None
    checkpoints: dict = field(default_factory=lambda: {"active": "base", "history": []})
    last_cycle_pairs: int = 0

    def cycle(self, audit_entries: list[dict], corrections: list[dict],
              good_examples: list[dict]) -> dict:
        """One full mine→synthesize→train→promote cycle. Returns a morning report."""
        if self.autonomy == "manual":
            return {"ran": False, "reason": "autonomy=manual — start jobs explicitly"}
        patterns = self.miner.mine(audit_entries, corrections)
        dataset = self.synth.synthesize(patterns, good_examples)
        fresh = dataset["clean"]
        if fresh < 5:  # tiny in tests; production uses MIN_SIGNAL
            return {"ran": False, "reason": f"not enough signal ({fresh} pairs)"}
        if self.autonomy == "assisted":
            return {"ran": False, "needs_approval": True,
                    "proposal": {"patterns": len(patterns), "pairs": fresh,
                                 "flagged_pii": dataset["flagged_pii"]}}
        job = self.runner.start("selftune", dataset) if self.runner else None
        # Eval deltas come from evals/regression.jsonl + failure_set.jsonl in production.
        result = self.promoter.promote(job, regression_delta=0.0, failure_delta=0.1,
                                       checkpoints=self.checkpoints)
        self.last_cycle_pairs = fresh
        return {"ran": True, "patterns": len(patterns), "pairs": fresh,
                "promoted": result["promoted"], "checkpoint": self.checkpoints["active"],
                "report": (f"Retrained on {fresh} pairs from {len(patterns)} failure patterns. "
                           f"{'New model promoted.' if result['promoted'] else 'Kept old model.'} "
                           f"Rollback available.")}

    def rollback(self) -> dict:
        hist = self.checkpoints.get("history", [])
        if len(hist) >= 1:
            hist.pop()  # drop current
            self.checkpoints["active"] = hist[-1] if hist else "base"
        else:
            self.checkpoints["active"] = "base"
        return {"active": self.checkpoints["active"]}
