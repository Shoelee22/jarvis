"""Dataset builder: docs + corrections + dictated Q&A → training pairs, with PII scan."""
from __future__ import annotations
import re
import uuid
from pathlib import Path

PII_PATTERNS = {
    "email": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    "phone": re.compile(r"\+?\d[\d\s-]{7,}\d"),
    "card": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
}


def pii_scan(text: str) -> list[str]:
    return [name for name, pat in PII_PATTERNS.items() if pat.search(text)]


def pair_from_correction(prompt: str, bad: str, good: str) -> dict:
    return {"id": uuid.uuid4().hex[:12], "kind": "correction",
            "prompt": prompt,
            "response": f"### Wrong\n{bad}\n### Right\n{good}",
            "pii_flags": pii_scan(prompt + good)}


def pair_from_doc(chunk: str, source: str) -> dict:
    return {"id": uuid.uuid4().hex[:12], "kind": "doc",
            "prompt": f"Based on my documents, explain: {chunk[:120]}...",
            "response": chunk, "pii_flags": pii_scan(chunk)}


def build_dataset(pairs: list[dict], min_pairs: int = 50) -> dict:
    flagged = [p for p in pairs if p.get("pii_flags")]
    clean = [p for p in pairs if not p.get("pii_flags")]
    return {"total": len(pairs), "clean": len(clean),
            "flagged_pii": len(flagged),
            "ready": len(clean) >= min_pairs,
            "pairs": clean,
            "flagged_examples": flagged[:5]}
