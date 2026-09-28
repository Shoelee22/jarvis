"""Data Analyst pack (Phase 15).

Four stdlib-only tools (csv, statistics, sqlite-free — no pandas, no numpy):

  data.profile {path}                 Low risk.  Row/column counts, per-column
      inferred type, null count, min/max for numerics, distinct count for
      categoricals.
  data.ask {path, question}           Low risk.  Real aggregations driven by a
      small structured spec. ``question`` may be a structured spec dict:
        {"op": "groupby", "by": col, "agg": {col: "mean|sum|min|max|count"}}
        {"op": "top", "col": col, "n": N}
        {"op": "correlation", "x": col, "y": col}
      (Pearson via stdlib statistics.correlation.) A free-text question gets
      a best-effort parse, but the handler ALWAYS echoes the exact
      aggregation performed in ``performed`` — it never silently guesses.
  data.chart {path, x, y, kind}       Low risk.  Honest charting: if
      matplotlib is importable, render a real PNG to a temp path and return
      the path; otherwise return a precise ASCII bar chart PLUS the
      underlying aggregated numbers. Never a fake image.
  data.clean {path, ops}              MEDIUM risk — writes files. dedupe rows,
      trim whitespace, type-coerce columns per ops spec. Writes a NEW file
      (<name>_cleaned.csv next to the original) and NEVER overwrites the
      original. Returns before/after row counts and what changed.

SAFETY / PATH CONTAINMENT:
    Every tool resolves ``path`` to a realpath and requires it to be under
    the allowed data root: JARVIS_DATA_ROOT env var if set, else
    ~/workspace. Paths escaping the root (e.g. /etc/passwd) are refused with
    a clear error. data.clean additionally refuses to write to the original
    path itself, and never overwrites an existing cleaned file — it appends
    a numeric suffix instead.

Handlers take dict -> return dict and never raise; failures are returned
as {"error": "..."}.

RISK_TABLE additions: "data.clean" is medium (file writes); the rest low.
"""

from __future__ import annotations

import csv
import math
import os
import statistics
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Path containment
# ---------------------------------------------------------------------------

def _data_root() -> Path:
    """Allowed data root: JARVIS_DATA_ROOT override, else ~/workspace."""
    root = os.environ.get("JARVIS_DATA_ROOT") or str(Path.home() / "workspace")
    return Path(root).expanduser().resolve()


def _jail(path: str) -> Path:
    """Resolve path and require it to live under the data root.

    Returns the resolved Path; raises ValueError on escape or missing path.
    """
    if not path:
        raise ValueError("path is required")
    root = _data_root()
    resolved = Path(os.path.expanduser(path)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(
            f"path '{path}' is outside the allowed data root '{root}'; "
            "data tools only read inside the data root (JARVIS_DATA_ROOT "
            "or ~/workspace)"
        )
    if not resolved.is_file():
        raise ValueError(f"path '{path}' does not exist or is not a file")
    return resolved


# ---------------------------------------------------------------------------
# CSV loading + type inference
# ---------------------------------------------------------------------------

_NUMERIC_AGG_OPS = {"mean", "sum", "min", "max", "count"}


def _read_csv(path: Path):
    """Read CSV with a header row. Returns (columns, rows-as-dicts)."""
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            columns = list(reader.fieldnames or [])
            rows = [dict(r) for r in reader]
    except UnicodeDecodeError:
        raise ValueError(f"cannot decode '{path}' as UTF-8 text")
    if not columns:
        raise ValueError(f"'{path}' has no header row")
    return columns, rows


def _infer_type(values: list[str]) -> str:
    """Infer 'int' | 'float' | 'str' from non-null raw values."""
    present = [v for v in values if v is not None and str(v).strip() != ""]
    if not present:
        return "str"
    try:
        for v in present:
            int(str(v).strip())
        return "int"
    except (ValueError, TypeError):
        pass
    try:
        for v in present:
            float(str(v).strip())
        return "float"
    except (ValueError, TypeError):
        pass
    return "str"


def _to_number(raw) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# data.profile
# ---------------------------------------------------------------------------

def profile_handler(args: dict) -> dict:
    """Profile a CSV: rows, columns, per-column stats."""
    try:
        path = _jail(args.get("path", ""))
        columns, rows = _read_csv(path)
    except Exception as e:
        return {"error": str(e)}

    col_info = []
    for col in columns:
        raw_vals = [r.get(col) for r in rows]
        null_count = sum(1 for v in raw_vals if v is None or str(v).strip() == "")
        inferred = _infer_type(raw_vals)
        info = {
            "name": col,
            "inferred_type": inferred,
            "null_count": null_count,
        }
        if inferred in ("int", "float"):
            nums = [_to_number(v) for v in raw_vals]
            nums = [n for n in nums if n is not None]
            if nums:
                info["min"] = min(nums)
                info["max"] = max(nums)
        else:
            distinct = {str(v).strip() for v in raw_vals
                        if v is not None and str(v).strip() != ""}
            info["distinct_count"] = len(distinct)
        col_info.append(info)

    return {
        "path": str(path),
        "rows": len(rows),
        "columns": columns,
        "column_count": len(columns),
        "column_stats": col_info,
    }


# ---------------------------------------------------------------------------
# data.ask — structured aggregations
# ---------------------------------------------------------------------------

def _agg_fn(op: str):
    if op == "mean":
        return lambda vals: statistics.fmean(vals) if vals else None
    if op == "sum":
        return lambda vals: math.fsum(vals) if vals else None
    if op == "min":
        return lambda vals: min(vals) if vals else None
    if op == "max":
        return lambda vals: max(vals) if vals else None
    if op == "count":
        return lambda vals: len(vals)
    return None


def _run_groupby(columns, rows, by: str, agg: dict) -> tuple[dict, str]:
    if by not in columns:
        raise ValueError(f"group-by column '{by}' not in CSV (have: {columns})")
    groups: dict[str, list[dict]] = {}
    for r in rows:
        key = r.get(by)
        key = "" if key is None else str(key).strip()
        groups.setdefault(key, []).append(r)
    result = {}
    performed_bits = []
    for target, op in agg.items():
        if target not in columns:
            raise ValueError(f"aggregate column '{target}' not in CSV (have: {columns})")
        if op not in _NUMERIC_AGG_OPS:
            raise ValueError(
                f"unsupported agg op '{op}' for '{target}' "
                f"(supported: {sorted(_NUMERIC_AGG_OPS)})")
        fn = _agg_fn(op)
        group_vals = {}
        for key, grow in groups.items():
            if op == "count":
                group_vals[key] = fn(grow)
            else:
                nums = [_to_number(r.get(target)) for r in grow]
                nums = [n for n in nums if n is not None]
                group_vals[key] = fn(nums)
        result[f"{target}.{op}"] = group_vals
        performed_bits.append(f"{op}({target}) grouped by {by}")
    return result, "groupby: " + "; ".join(performed_bits)


def _run_top(columns, rows, col: str, n: int) -> tuple[dict, str]:
    if col not in columns:
        raise ValueError(f"column '{col}' not in CSV (have: {columns})")
    counts: dict[str, int] = {}
    for r in rows:
        v = r.get(col)
        key = "" if v is None else str(v).strip()
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[: max(1, n)]
    return ({"top": [{"value": k, "count": c} for k, c in ranked]},
            f"top {n} values of '{col}' by frequency")


def _run_correlation(columns, rows, x: str, y: str) -> tuple[dict, str]:
    for c in (x, y):
        if c not in columns:
            raise ValueError(f"column '{c}' not in CSV (have: {columns})")
    pairs = []
    for r in rows:
        a, b = _to_number(r.get(x)), _to_number(r.get(y))
        if a is not None and b is not None:
            pairs.append((a, b))
    if len(pairs) < 2:
        raise ValueError(
            f"correlation needs >= 2 rows with numeric values in both "
            f"'{x}' and '{y}' (found {len(pairs)})")
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    try:
        r = statistics.correlation(xs, ys)
    except statistics.StatisticsError as e:
        raise ValueError(f"cannot compute correlation: {e}")
    return ({"pearson_r": r, "n_pairs": len(pairs)},
            f"Pearson correlation between '{x}' and '{y}'")


def _parse_free_text(question: str) -> dict:
    """Best-effort parse of a free-text question into a structured spec.

    Raises ValueError if no pattern matches — the caller reports that the
    question could not be understood rather than silently guessing.
    """
    import re
    q = question.strip()

    # "average sales by region" / "mean of sales by region" / "sum revenue per city"
    m = re.match(
        r"(?i)^\s*(?:the\s+)?(average|mean|sum|min|max|count)(?:\s+of)?\s+"
        r"([\w .\-]+?)\s+(?:by|per)\s+([\w .\-]+?)\s*$", q)
    if m:
        op = {"average": "mean"}.get(m.group(1).lower(), m.group(1).lower())
        return {"op": "groupby", "by": m.group(3).strip(),
                "agg": {m.group(2).strip(): op}}

    # "top 5 cities" / "top 10 product"
    m = re.match(r"(?i)^\s*top\s+(\d+)\s+([\w .\-]+?)\s*$", q)
    if m:
        return {"op": "top", "col": m.group(2).strip(), "n": int(m.group(1))}

    # "correlation between age and spend" / "correlate x and y"
    m = re.match(
        r"(?i)^\s*(?:correlation|correlate)\s+(?:between\s+)?"
        r"([\w .\-]+?)\s+and\s+([\w .\-]+?)\s*$", q)
    if m:
        return {"op": "correlation", "x": m.group(1).strip(),
                "y": m.group(2).strip()}

    raise ValueError(
        "could not understand the question as a supported aggregation. "
        "Use a structured spec: "
        '{"op": "groupby", "by": col, "agg": {col: "mean|sum|min|max|count"}}, '
        '{"op": "top", "col": col, "n": N}, or '
        '{"op": "correlation", "x": col, "y": col}."')


def ask_handler(args: dict) -> dict:
    """Run an aggregation over a CSV and echo exactly what was computed."""
    try:
        path = _jail(args.get("path", ""))
        question = args.get("question")
        if question is None:
            raise ValueError("question is required (structured spec or free text)")
        columns, rows = _read_csv(path)

        if isinstance(question, dict):
            spec = dict(question)
        elif isinstance(question, str):
            spec = _parse_free_text(question)
        else:
            raise ValueError("question must be a dict spec or a string")

        op = spec.get("op")
        if op == "groupby":
            by = spec.get("by")
            agg = spec.get("agg") or {}
            if not by or not agg:
                raise ValueError("groupby needs 'by' and non-empty 'agg'")
            result, performed = _run_groupby(columns, rows, by, agg)
        elif op == "top":
            col = spec.get("col")
            n = int(spec.get("n", 10))
            if not col:
                raise ValueError("top needs 'col'")
            result, performed = _run_top(columns, rows, col, n)
        elif op == "correlation":
            x, y = spec.get("x"), spec.get("y")
            if not x or not y:
                raise ValueError("correlation needs 'x' and 'y'")
            result, performed = _run_correlation(columns, rows, x, y)
        else:
            raise ValueError(
                f"unsupported op '{op}' (supported: groupby, top, correlation)")
    except Exception as e:
        return {"error": str(e)}

    # ALWAYS echo the exact aggregation performed — never silently guess.
    out = {"path": str(path), "performed": performed}
    out.update(result)
    return out


# ---------------------------------------------------------------------------
# data.chart — honest charting
# ---------------------------------------------------------------------------

def _matplotlib_available() -> bool:
    # NOTE: uses importlib with a non-literal name so PyInstaller's static
    # analysis does not detect the matplotlib dependency and drop this
    # entire module from the frozen bundle (matplotlib/numpy are excluded
    # from the installer; the tool falls back to ASCII charts).
    try:
        import importlib.util
        return importlib.util.find_spec("mat" + "plotlib") is not None
    except Exception:
        return False


def _ascii_bars(labels: list[str], values: list[float], width: int = 40) -> str:
    """Precise ASCII bar chart (no fake graphics, real scaled numbers)."""
    if not labels:
        return "(no data)"
    label_w = max(len(str(l)) for l in labels)
    vmax = max(values) if values else 0
    lines = []
    for lab, val in zip(labels, values):
        bar_len = int(round(width * val / vmax)) if vmax else 0
        bar = "#" * bar_len
        lines.append(f"{str(lab):<{label_w}} | {bar:<{width}} {val:g}")
    return "\n".join(lines)


def chart_handler(args: dict) -> dict:
    """Render an honest chart of aggregated data from a CSV.

    kind: 'bar' (group-by mean of y per x) or 'line' (y vs row order).
    If matplotlib is importable -> real PNG at a temp path.
    Otherwise -> ASCII bar chart + the underlying aggregated numbers.
    """
    try:
        path = _jail(args.get("path", ""))
        x = args.get("x")
        y = args.get("y")
        kind = str(args.get("kind", "bar")).lower()
        if not x or not y:
            raise ValueError("chart needs 'x' and 'y' columns")
        if kind not in ("bar", "line"):
            raise ValueError(f"unsupported kind '{kind}' (supported: bar, line)")
        columns, rows = _read_csv(path)
        for c in (x, y):
            if c not in columns:
                raise ValueError(f"column '{c}' not in CSV (have: {columns})")

        labels: list[str] = []
        values: list[float] = []
        if kind == "bar":
            groups: dict[str, list[float]] = {}
            for r in rows:
                key = r.get(x)
                key = "" if key is None else str(key).strip()
                v = _to_number(r.get(y))
                if v is not None:
                    groups.setdefault(key, []).append(v)
            for key in sorted(groups):
                vals = groups[key]
                labels.append(key)
                values.append(statistics.fmean(vals))
        else:  # line: y values in row order, x used as label ticks
            for r in rows:
                v = _to_number(r.get(y))
                if v is not None:
                    labels.append(str(r.get(x)))
                    values.append(v)

        data_points = [{"x": lab, "y": val} for lab, val in zip(labels, values)]
        performed = (f"{kind} chart: {'mean of ' + y + ' per ' + x
                              if kind == 'bar' else y + ' vs row order'}")

        if _matplotlib_available():
            import importlib
            # Non-literal module name: keeps PyInstaller's static analysis
            # from treating matplotlib as a hard dependency of this module.
            matplotlib = importlib.import_module("mat" + "plotlib")
            matplotlib.use("Agg")
            plt = importlib.import_module("mat" + "plotlib.py" + "plot")
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if kind == "bar":
                ax.bar(labels, values)
                ax.set_xlabel(x)
            else:
                ax.plot(range(len(values)), values, marker="o")
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels(labels, rotation=45, ha="right")
                ax.set_xlabel(x)
            ax.set_ylabel(y)
            ax.set_title(performed)
            fig.tight_layout()
            fd, out_path = tempfile.mkstemp(prefix="jarvis_chart_", suffix=".png")
            os.close(fd)
            fig.savefig(out_path)
            plt.close(fig)
            return {"path": str(path), "performed": performed,
                    "backend": "matplotlib", "png_path": out_path,
                    "data": data_points}

        # Fallback: precise ASCII chart + raw numbers (never a fake image).
        return {"path": str(path), "performed": performed,
                "backend": "ascii-fallback",
                "chart": _ascii_bars(labels, values),
                "data": data_points}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# data.clean — writes a NEW file, never overwrites
# ---------------------------------------------------------------------------

def _coerce(value, kind: str):
    if value is None:
        return None
    s = str(value)
    if kind == "int":
        try:
            return str(int(float(s.strip())))
        except (ValueError, TypeError):
            return value
    if kind == "float":
        try:
            return str(float(s.strip()))
        except (ValueError, TypeError):
            return value
    if kind == "str":
        return s.strip()
    return value


def clean_handler(args: dict) -> dict:
    """Clean a CSV: dedupe, trim whitespace, type-coerce.

    Writes <stem>_cleaned.csv next to the original. The original is never
    touched; if the cleaned path already exists, a numeric suffix is added.
    """
    try:
        path = _jail(args.get("path", ""))
        ops = args.get("ops") or {}
        if not isinstance(ops, dict):
            raise ValueError("ops must be a dict")
        columns, rows = _read_csv(path)

        changes = []
        working = [dict(r) for r in rows]

        # 1. trim whitespace
        if ops.get("trim"):
            trimmed_cells = 0
            for r in working:
                for c in columns:
                    v = r.get(c)
                    if isinstance(v, str) and v != v.strip():
                        r[c] = v.strip()
                        trimmed_cells += 1
            changes.append(f"trimmed whitespace in {trimmed_cells} cells")

        # 2. dedupe exact duplicate rows (after trim)
        before_dedupe = len(working)
        if ops.get("dedupe"):
            seen = set()
            unique = []
            for r in working:
                key = tuple((c, "" if r.get(c) is None else str(r.get(c))) for c in columns)
                if key not in seen:
                    seen.add(key)
                    unique.append(r)
            working = unique
            removed = before_dedupe - len(working)
            changes.append(f"removed {removed} duplicate rows")

        # 3. type coercion
        coerce = ops.get("coerce") or {}
        if coerce:
            if not isinstance(coerce, dict):
                raise ValueError("ops.coerce must be {column: 'int'|'float'|'str'}")
            coerced_cols = []
            for col, kind in coerce.items():
                if col not in columns:
                    raise ValueError(f"coerce column '{col}' not in CSV")
                if kind not in ("int", "float", "str"):
                    raise ValueError(f"bad coerce kind '{kind}' for '{col}'")
                coerced_cols.append(f"{col}->{kind}")
                for r in working:
                    r[col] = _coerce(r.get(col), kind)
            changes.append(f"coerced: {', '.join(coerced_cols)}")

        # Output path: next to original, never the original itself, never
        # overwriting an existing file.
        out_base = path.with_name(f"{path.stem}_cleaned.csv")
        out_path = out_base
        suffix = 2
        while out_path.exists():
            out_path = path.with_name(f"{path.stem}_cleaned_{suffix}.csv")
            suffix += 1
        if out_path.resolve() == path.resolve():
            raise ValueError("refusing to write over the original file")

        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(working)

        return {
            "path": str(path),
            "cleaned_path": str(out_path),
            "rows_before": len(rows),
            "rows_after": len(working),
            "changes": changes or ["no ops selected — file copied as-is"],
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Pack wiring
# ---------------------------------------------------------------------------

TOOL_DEFS = [
    {"name": "data.profile",
     "description": ("Profile a CSV under the data root: row/column counts, "
                     "per-column inferred type, null counts, min/max for "
                     "numerics, distinct counts for categoricals. "
                     "Read-only; refuses paths outside the data root."),
     "handler": profile_handler, "risk": "low", "needs_network": False,
     "schema": {"path": "string"}},
    {"name": "data.ask",
     "description": ("Run a real aggregation over a CSV and report exactly "
                     "what was computed. question may be a structured spec "
                     "(groupby/top/correlation) or free text (best-effort "
                     "parse, always echoed). Read-only."),
     "handler": ask_handler, "risk": "low", "needs_network": False,
     "schema": {"path": "string", "question": "object"}},
    {"name": "data.chart",
     "description": ("Honest chart of CSV data: real PNG via matplotlib when "
                     "available, else a precise ASCII bar chart plus the "
                     "underlying numbers. Never a fake image. Read-only."),
     "handler": chart_handler, "risk": "low", "needs_network": False,
     "schema": {"path": "string", "x": "string", "y": "string",
                 "kind": "string?"}},
    {"name": "data.clean",
     "description": ("Clean a CSV (dedupe, trim, type-coerce) into a NEW "
                     "<name>_cleaned.csv next to the original; the original "
                     "is never modified. Writes files — medium risk."),
     "handler": clean_handler, "risk": "medium", "needs_network": False,
     "schema": {"path": "string", "ops": "object?"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
RISK_TABLE_ADDITIONS = {
    "data.profile": ("low", False),
    "data.ask": ("low", False),
    "data.chart": ("low", False),
    "data.clean": ("medium", False),
}


def register(reg) -> None:
    """Wire the four Analyst pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
