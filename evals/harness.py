#!/usr/bin/env python3
"""Eval harness: JSONL in, scored out. Suites: regression, failure, injection."""
import json, sys, argparse

def load_suite(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]

def check_policy_case(case):
    """Injection-resistance: agent must NOT follow instructions in untrusted tool output."""
    from jarvis.agent.policy import PolicyEngine
    pe = PolicyEngine()
    # Simulate: tool output containing an injected instruction
    injected = case["tool_output"]
    verdict = pe.scan_untrusted(injected)
    # pass if the engine flags it as containing an instruction attempt
    return verdict["contains_instruction"] == case["expect_flag"]

def check_regression_case(case):
    kind = case.get("kind", "")
    if kind == "policy":
        from jarvis.agent.policy import PolicyEngine
        pe = PolicyEngine()
        d = pe.decide(case["tool"], case.get("args", {}))
        return d["action"] == case["expect"]
    if kind == "memory":
        from jarvis.memory.store import MemoryStore
        ms = MemoryStore(":memory:")
        ms.remember("fact", case["text"], source="test")
        hits = ms.search(case["query"])
        ms.close()
        return any(case["expect_in"] in h["text"] for h in hits)
    if kind == "selftune_gate":
        from jarvis.trainer.selftune import Promoter
        p = Promoter()
        return p.should_promote(case["regression_delta"], case["failure_delta"]) == case["expect"]
    if kind == "injection":
        return check_policy_case(case)
    if kind == "failure" or "pattern" in case:
        # failure-set entries must be mineable by the self-tuner
        from jarvis.trainer.selftune import Miner
        corrections = [{"prompt": case["pattern"], "bad_response": "",
                        "correction": case["user_correction"]}]
        return len(Miner().mine([], corrections)) > 0
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="regression", choices=["regression", "failure"])
    args = ap.parse_args()
    sys.path.insert(0, "sidecar")
    path = f"evals/{args.suite}.jsonl" if args.suite == "regression" else "evals/failure_set.jsonl"
    cases = load_suite(path)
    passed = failed = 0
    for c in cases:
        try:
            ok = check_regression_case(c)
        except Exception as e:
            ok = False
            c["error"] = str(e)
        if ok: passed += 1
        else:
            failed += 1
            print(f"FAIL [{c['id']}] {c.get('desc','')}" + (f" — {c.get('error')}" if "error" in c else ""))
    print(f"\n{args.suite}: {passed} passed, {failed} failed / {len(cases)}")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
