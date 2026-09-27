"""Finance Tool Pack (Phase 10): SIP / lump-sum / EMI calculators + expense summary.

Pure real math, low risk. Money is handled in integer paise internally
(round-half-up to the paise); displayed as rupees. No floats are trusted
for money beyond the final 2-decimal display.

finance.budget_summary reads the SAME sqlite store that data_pack's
expenses.add/expenses.report use: <DATA_DIR>/finance.db, table expenses
(amount_paise, category, note, created_at). DATA_DIR is read from
jarvis.config at call time (same as data_pack) so tests can monkeypatch it.
"""
from __future__ import annotations

import re
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


# ------------------------------------------------------------ helpers
def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


def _num(value, label: str):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{label} must be a number")
    return Decimal(str(value))


def _positive(value, label: str) -> Decimal:
    v = _num(value, label)
    if v <= 0:
        raise ValueError(f"{label} must be > 0")
    return v


def _paise(v: Decimal) -> int:
    """Round-half-up to integer paise."""
    return int((v * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _rs(paise: int) -> str:
    return f"\u20b9{paise / 100:,.2f}"


def _data_dir() -> Path:
    """DATA_DIR read at call time so tests can monkeypatch jarvis.config."""
    from ... import config
    return Path(config.DATA_DIR)


# ------------------------------------------------------ finance.sip
def finance_sip(args: dict) -> dict:
    """SIP future value (beginning-of-month contributions, monthly compounding)."""
    try:
        monthly = _positive(args["monthly"], "monthly")
        rate = _num(args.get("annual_return_pct", 0), "annual_return_pct")
        years = _positive(args["years"], "years")
        if rate < 0 or rate > 100:
            return {"error": "annual_return_pct must be between 0 and 100"}
        months = int(years * 12)
        r = rate / Decimal(100) / Decimal(12)
        if r == 0:
            fv = monthly * months
        else:
            one = Decimal(1)
            fv = monthly * ((one + r) ** months - 1) / r * (one + r)
        invested = monthly * months
        fv_paise = _paise(fv)
        invested_paise = _paise(invested)
        # Year-end schedule (end value after each full year).
        schedule = []
        for y in range(1, int(years) + 1):
            m = y * 12
            if r == 0:
                yfv = monthly * m
            else:
                one = Decimal(1)
                yfv = monthly * ((one + r) ** m - 1) / r * (one + r)
            yfv_p = _paise(yfv)
            schedule.append({"year": y, "end_value_paise": yfv_p,
                             "end_value_rupees": _rs(yfv_p)})
        return {"monthly_paise": _paise(monthly), "annual_return_pct": float(rate),
                "years": int(years), "months": months,
                "invested_paise": invested_paise,
                "invested_rupees": _rs(invested_paise),
                "future_value_paise": fv_paise,
                "future_value_rupees": _rs(fv_paise),
                "gains_paise": fv_paise - invested_paise,
                "gains_rupees": _rs(fv_paise - invested_paise),
                "yearly_schedule": schedule,
                "method": "FV = monthly * (((1+r)^n - 1)/r) * (1+r), "
                          "r = annual/12, contributions at month start"}
    except KeyError as e:
        return {"error": f"missing required arg: {e}"}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return _fail("finance.sip", e)


# --------------------------------------------------- finance.lumpsum
def finance_lumpsum(args: dict) -> dict:
    """Lump-sum future value (annual compounding)."""
    try:
        principal = _positive(args["principal"], "principal")
        rate = _num(args.get("annual_return_pct", 0), "annual_return_pct")
        years = _positive(args["years"], "years")
        if rate < 0 or rate > 100:
            return {"error": "annual_return_pct must be between 0 and 100"}
        fv = principal * (1 + rate / 100) ** years
        p_paise = _paise(principal)
        fv_paise = _paise(fv)
        schedule = []
        for y in range(1, int(years) + 1):
            yfv_p = _paise(principal * (1 + rate / 100) ** y)
            schedule.append({"year": y, "end_value_paise": yfv_p,
                             "end_value_rupees": _rs(yfv_p)})
        return {"principal_paise": p_paise, "principal_rupees": _rs(p_paise),
                "annual_return_pct": float(rate), "years": int(years),
                "future_value_paise": fv_paise,
                "future_value_rupees": _rs(fv_paise),
                "gains_paise": fv_paise - p_paise,
                "gains_rupees": _rs(fv_paise - p_paise),
                "yearly_schedule": schedule,
                "method": "FV = P * (1 + r)^years, r = annual/100"}
    except KeyError as e:
        return {"error": f"missing required arg: {e}"}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return _fail("finance.lumpsum", e)


# ------------------------------------------------------- finance.emi
def finance_emi(args: dict) -> dict:
    """Loan EMI (reducing balance)."""
    try:
        principal = _positive(args["principal"], "principal")
        rate = _num(args.get("annual_rate_pct", 0), "annual_rate_pct")
        months = int(args["months"])
        if months <= 0:
            return {"error": "months must be > 0"}
        if rate < 0 or rate > 100:
            return {"error": "annual_rate_pct must be between 0 and 100"}
        r = rate / Decimal(100) / Decimal(12)
        if r == 0:
            emi = principal / months
        else:
            one = Decimal(1)
            emi = principal * r * (one + r) ** months / ((one + r) ** months - 1)
        emi_paise = _paise(emi)
        total_paise = emi_paise * months
        p_paise = _paise(principal)
        return {"principal_paise": p_paise, "principal_rupees": _rs(p_paise),
                "annual_rate_pct": float(rate), "months": months,
                "emi_paise": emi_paise, "emi_rupees": _rs(emi_paise),
                "total_payable_paise": total_paise,
                "total_payable_rupees": _rs(total_paise),
                "total_interest_paise": total_paise - p_paise,
                "total_interest_rupees": _rs(total_paise - p_paise),
                "method": "EMI = P*r*(1+r)^n/((1+r)^n-1), r = annual/12"}
    except KeyError as e:
        return {"error": f"missing required arg: {e}"}
    except (ValueError, TypeError) as e:
        return {"error": str(e)}
    except Exception as e:
        return _fail("finance.emi", e)


# --------------------------------------------- finance.budget_summary
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def finance_budget_summary(args: dict) -> dict:
    """Summarize the expenses store (same sqlite as data_pack expenses)."""
    try:
        month = args.get("month")
        where, params = "", []
        if month is not None:
            month = str(month)
            if not _MONTH_RE.match(month):
                return {"error": "month must be 'YYYY-MM'"}
            where, params = "WHERE strftime('%Y-%m', created_at) = ?", [month]
        db = _data_dir() / "finance.db"
        if not db.is_file():
            return {"error": "no expenses store found yet (no finance.db). "
                             "Log expenses first with expenses.add."}
        conn = sqlite3.connect(db)
        try:
            tables = {r[0] for r in
                      conn.execute("SELECT name FROM sqlite_master "
                                   "WHERE type='table'").fetchall()}
            if "expenses" not in tables:
                return {"error": "expenses table not found in finance.db"}
            rows = conn.execute(
                f"SELECT category, SUM(amount_paise), COUNT(*) FROM expenses "
                f"{where} GROUP BY category ORDER BY category", params).fetchall()
            grand_row = conn.execute(
                f"SELECT COALESCE(SUM(amount_paise),0), COUNT(*) FROM expenses "
                f"{where}", params).fetchone()
        finally:
            conn.close()
        if grand_row[1] == 0:
            return {"month": month, "note": "no expenses recorded yet",
                    "by_category": [], "grand_total_paise": 0,
                    "grand_total_rupees": _rs(0), "expense_count": 0}
        by_category = [{"category": c, "total_paise": t,
                        "total_rupees": _rs(t), "count": n}
                       for c, t, n in rows]
        grand = grand_row[0]
        top = max(by_category, key=lambda c: c["total_paise"])["category"]
        return {"month": month, "by_category": by_category,
                "grand_total_paise": grand, "grand_total_rupees": _rs(grand),
                "expense_count": grand_row[1], "top_category": top,
                "source": str(db)}
    except Exception as e:
        return _fail("finance.budget_summary", e)


# ------------------------------------------------- registry wiring metadata
TOOL_DEFS = [
    {"name": "finance.sip",
     "description": "SIP future value from monthly amount, annual return %, "
                    "years (integer-paise math, yearly schedule).",
     "handler": finance_sip, "risk": "low", "needs_network": False,
     "schema": {"monthly": "number", "annual_return_pct": "number",
                "years": "number"}},
    {"name": "finance.lumpsum",
     "description": "Lump-sum future value from principal, annual return %, "
                    "years.",
     "handler": finance_lumpsum, "risk": "low", "needs_network": False,
     "schema": {"principal": "number", "annual_return_pct": "number",
                "years": "number"}},
    {"name": "finance.emi",
     "description": "Loan EMI, total payable and total interest from "
                    "principal, annual rate %, months.",
     "handler": finance_emi, "risk": "low", "needs_network": False,
     "schema": {"principal": "number", "annual_rate_pct": "number",
                "months": "int"}},
    {"name": "finance.budget_summary",
     "description": "Summarize the expenses store by category "
                    "(reads the same sqlite as expenses.add).",
     "handler": finance_budget_summary, "risk": "low", "needs_network": False,
     "schema": {"month": "string?"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(finance_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "finance.sip": ("low", False),
    "finance.lumpsum": ("low", False),
    "finance.emi": ("low", False),
    "finance.budget_summary": ("low", False),
}


def register(reg) -> None:
    """Wire the four finance tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))


__all__ = ["TOOL_DEFS", "RISK_TABLE_ADDITIONS", "register",
           "finance_sip", "finance_lumpsum", "finance_emi",
           "finance_budget_summary"]
