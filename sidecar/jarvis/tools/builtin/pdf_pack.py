"""PDF Tool Pack (Phase 10): merge, split, text extraction, file info.

Requires the ``pypdf`` package (``pip install pypdf``). Without it every
handler returns an honest ``{"error": ...}`` install hint — nothing is faked.

Risk notes: pdf.extract_text / pdf.info are read-only ("low");
pdf.merge / pdf.split write files ("medium").

Page specs for split/extract: a comma list of 1-based pages and ranges,
e.g. "1-3,5,8-". Omit to use all pages.
"""
from __future__ import annotations

from pathlib import Path

_INSTALL_HINT = ("pypdf is not installed. Install it with: pip install pypdf")


# ------------------------------------------------------------ helpers
def _pypdf():
    """Return the pypdf module, or None when it is not installed."""
    try:
        import pypdf
        return pypdf
    except ImportError:
        return None


def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


def _need_file(value, label: str) -> tuple[Path | None, str | None]:
    raw = str(value or "").strip()
    if not raw:
        return None, f"{label} is required"
    p = Path(raw).expanduser()
    if not p.is_file():
        return None, f"{label} not found: {raw}"
    return p, None


def _parse_pages(spec, n_pages: int):
    """Parse a page spec into a sorted unique list of 0-based indices.

    Returns (pages, error). pages=None means "all pages" when spec is empty.
    """
    if spec is None or (isinstance(spec, str) and not spec.strip()):
        return None, None
    items = spec if isinstance(spec, list) else str(spec).split(",")
    pages: set[int] = set()
    for raw in items:
        token = str(raw).strip()
        if not token:
            continue
        if "-" in token:
            lo_s, _, hi_s = token.partition("-")
            lo = int(lo_s.strip() or "1")
            hi = int(hi_s.strip() or str(n_pages))
            if lo < 1 or hi > n_pages or lo > hi:
                return None, f"page range '{token}' out of bounds (1-{n_pages})"
            pages.update(range(lo - 1, hi))
        else:
            try:
                num = int(token)
            except ValueError:
                return None, f"invalid page token: '{token}'"
            if num < 1 or num > n_pages:
                return None, f"page {num} out of bounds (1-{n_pages})"
            pages.add(num - 1)
    if not pages:
        return None, "no pages selected"
    return sorted(pages), None


def _guard_output(path_str, label: str) -> tuple[Path | None, str | None]:
    raw = str(path_str or "").strip()
    if not raw:
        return None, f"{label} is required"
    p = Path(raw).expanduser()
    if p.suffix.lower() != ".pdf":
        return None, f"{label} must end in .pdf: {raw}"
    return p, None


# ------------------------------------------------------- pdf.merge
def pdf_merge(args: dict) -> dict:
    """Merge 2+ PDF files into one output PDF."""
    try:
        pp = _pypdf()
        if pp is None:
            return {"error": _INSTALL_HINT}
        files = args.get("files")
        if not isinstance(files, list) or len(files) < 2:
            return {"error": "files must be a list of at least 2 PDF paths"}
        out, err = _guard_output(args.get("output"), "output")
        if err:
            return {"error": err}
        missing = [f for f in files if not Path(str(f)).expanduser().is_file()]
        if missing:
            return {"error": f"input file(s) not found: {missing[:3]}"}
        writer = pp.PdfWriter()
        pages_total = 0
        for f in files:
            reader = pp.PdfReader(str(Path(str(f)).expanduser()))
            pages_total += len(reader.pages)
            for page in reader.pages:
                writer.add_page(page)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as fh:
            writer.write(fh)
        return {"output": str(out), "files_merged": len(files),
                "pages_total": pages_total}
    except Exception as e:
        return _fail("pdf.merge", e)


# ------------------------------------------------------- pdf.split
def pdf_split(args: dict) -> dict:
    """Extract selected pages from a PDF into a new PDF."""
    try:
        pp = _pypdf()
        if pp is None:
            return {"error": _INSTALL_HINT}
        src, err = _need_file(args.get("file"), "file")
        if err:
            return {"error": err}
        out, err = _guard_output(args.get("output"), "output")
        if err:
            return {"error": err}
        reader = pp.PdfReader(str(src))
        n = len(reader.pages)
        pages, perr = _parse_pages(args.get("pages"), n)
        if perr:
            return {"error": perr}
        if pages is None:
            pages = list(range(n))
        writer = pp.PdfWriter()
        for i in pages:
            writer.add_page(reader.pages[i])
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as fh:
            writer.write(fh)
        return {"output": str(out), "source": str(src),
                "pages_extracted": [i + 1 for i in pages]}
    except Exception as e:
        return _fail("pdf.split", e)


# ------------------------------------------------------- pdf.extract_text
_TEXT_CAP = 200000  # chars across all returned pages


def pdf_extract_text(args: dict) -> dict:
    """Extract text from pages (read-only)."""
    try:
        pp = _pypdf()
        if pp is None:
            return {"error": _INSTALL_HINT}
        src, err = _need_file(args.get("file"), "file")
        if err:
            return {"error": err}
        reader = pp.PdfReader(str(src))
        n = len(reader.pages)
        pages, perr = _parse_pages(args.get("pages"), n)
        if perr:
            return {"error": perr}
        if pages is None:
            pages = list(range(n))
        out_pages = []
        chars = 0
        truncated = False
        for i in pages:
            text = reader.pages[i].extract_text() or ""
            if chars + len(text) > _TEXT_CAP:
                text = text[: max(0, _TEXT_CAP - chars)]
                truncated = True
            out_pages.append({"page": i + 1, "text": text})
            chars += len(text)
            if truncated:
                break
        return {"file": str(src), "pages": out_pages,
                "total_chars": chars, "truncated": truncated,
                "note": "scanned/image PDFs may return empty text "
                        "(no OCR is performed)"}
    except Exception as e:
        return _fail("pdf.extract_text", e)


# ------------------------------------------------------- pdf.info
def pdf_info(args: dict) -> dict:
    """Page count, metadata, encryption flag, size (read-only)."""
    try:
        pp = _pypdf()
        if pp is None:
            return {"error": _INSTALL_HINT}
        src, err = _need_file(args.get("file"), "file")
        if err:
            return {"error": err}
        reader = pp.PdfReader(str(src))
        meta = {}
        try:
            for k, v in (reader.metadata or {}).items():
                meta[str(k).lstrip("/")] = str(v)
        except Exception:
            pass
        return {"file": str(src), "pages": len(reader.pages),
                "encrypted": bool(reader.is_encrypted),
                "size_bytes": src.stat().st_size,
                "metadata": meta}
    except Exception as e:
        return _fail("pdf.info", e)


# ------------------------------------------------- registry wiring metadata
TOOL_DEFS = [
    {"name": "pdf.merge",
     "description": "Merge 2+ PDF files into one output PDF (writes a file).",
     "handler": pdf_merge, "risk": "medium", "needs_network": False,
     "schema": {"files": "list", "output": "string"}},
    {"name": "pdf.split",
     "description": "Extract selected pages from a PDF into a new PDF "
                    "(writes a file).",
     "handler": pdf_split, "risk": "medium", "needs_network": False,
     "schema": {"file": "string", "pages": "string?", "output": "string"}},
    {"name": "pdf.extract_text",
     "description": "Extract text from PDF pages (read-only, no OCR).",
     "handler": pdf_extract_text, "risk": "low", "needs_network": False,
     "schema": {"file": "string", "pages": "string?"}},
    {"name": "pdf.info",
     "description": "PDF page count, metadata, encryption flag (read-only).",
     "handler": pdf_info, "risk": "low", "needs_network": False,
     "schema": {"file": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(pdf_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "pdf.merge": ("medium", False),
    "pdf.split": ("medium", False),
    "pdf.extract_text": ("low", False),
    "pdf.info": ("low", False),
}


def register(reg) -> None:
    """Wire the four PDF tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))


__all__ = ["TOOL_DEFS", "RISK_TABLE_ADDITIONS", "register",
           "pdf_merge", "pdf_split", "pdf_extract_text", "pdf_info"]
