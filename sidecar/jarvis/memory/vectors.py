"""Vector index: LanceDB when installed, else in-process brute-force embeddings.

Embeddings: hashed bag-of-words fallback (no model needed). Production swaps in
bge-small via the same interface.
"""
from __future__ import annotations
import hashlib
import math
from pathlib import Path

DIM = 256


def _embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for tok in text.lower().split():
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class VectorIndex:
    def __init__(self, dir_path: str | Path):
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._ids: list[str] = []
        self._vecs: list[list[float]] = []
        self._texts: dict[str, str] = {}
        self._lance = None
        try:
            import lancedb  # type: ignore
            self._lance = lancedb.connect(str(self.dir / "lance"))
        except ImportError:
            pass

    def add(self, id_: str, text: str):
        if self._lance:
            import pyarrow as pa  # type: ignore
            tbl = self._table()
            tbl.add([{"id": id_, "text": text, "vector": _embed(text)}])
        else:
            self._ids.append(id_)
            self._vecs.append(_embed(text))
            self._texts[id_] = text

    def _table(self):
        import pyarrow as pa  # type: ignore
        try:
            return self._lance.open_table("vectors")
        except Exception:
            schema = pa.schema([("id", pa.string()), ("text", pa.string()),
                                ("vector", pa.list_(pa.float32(), DIM))])
            return self._lance.create_table("vectors", schema=schema)

    def search(self, query: str, k: int = 5) -> list[str]:
        if self._lance:
            q = _embed(query)
            return [r["id"] for r in self._table().search(q).limit(k).to_list()]
        q = _embed(query)
        scored = sorted(
            ((sum(a * b for a, b in zip(q, v)), i) for i, v in enumerate(self._vecs)),
            reverse=True)
        # drop zero-similarity results so empty stores return []
        return [self._ids[i] for s, i in scored[:k] if s > 0]

    def remove(self, id_: str):
        if self._lance:
            self._table().delete(f"id = '{id_}'")
        elif id_ in self._texts:
            i = self._ids.index(id_)
            del self._ids[i]
            del self._vecs[i]
            del self._texts[id_]

    def close(self):
        pass
