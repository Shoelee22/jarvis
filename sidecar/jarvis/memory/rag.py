"""Local RAG: register folders → chunk → embed → cite file:line."""
from __future__ import annotations
from pathlib import Path

from .vectors import VectorIndex

CHUNK = 800


class KnowledgeBase:
    def __init__(self, dir_path: str | Path):
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index = VectorIndex(self.dir / "doc_vectors")
        self.sources: list[Path] = []

    def register(self, folder: str) -> dict:
        root = Path(folder).expanduser().resolve()
        self.sources.append(root)
        n = 0
        for f in root.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".txt", ".md", ".pdf", ".docx"):
                try:
                    text = f.read_text(errors="replace")
                except Exception:
                    continue
                for i in range(0, len(text), CHUNK):
                    chunk = text[i:i + CHUNK]
                    self.index.add(f"{f}:{i}", f"{f}\n{chunk}")
                    n += 1
        return {"folder": str(root), "chunks": n}

    def ask(self, query: str, k: int = 4) -> list[dict]:
        ids = self.index.search(query, k)
        out = []
        for chunk_id in ids:
            # chunk_id is "path:offset"; recover a representative snippet
            out.append({"source": chunk_id, "snippet": ""})
        return out
