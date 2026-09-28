"""Small safe tools: calculator, clipboard."""
from __future__ import annotations
import ast
import operator

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
       ast.Mod: operator.mod}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


def calc(args: dict) -> dict:
    return {"expression": args["expression"], "value": _eval(ast.parse(args["expression"], mode="eval"))}


def clipboard_read(args: dict) -> dict:
    # Shell owns the clipboard; sidecar asks via IPC in production.
    # Local fallback best-effort.
    try:
        import subprocess
        out = subprocess.run(["xclip", "-o"], capture_output=True, text=True, timeout=3)
        return {"content": out.stdout[:2000]}
    except Exception as e:
        return {"content": "", "note": f"clipboard unavailable: {e}"}
