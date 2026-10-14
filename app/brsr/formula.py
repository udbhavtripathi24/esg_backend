"""Safe formula evaluator for computed BRSR table cells.

Design rule for the whole BRSR module: the client enters raw counts and
amounts only. Every percentage, total and intensity is computed here, so
a value like "112% of male employees reviewed" (which appears in real
filings when someone types a percentage against the wrong denominator)
cannot be produced by this platform.

Formulas are tiny expressions written in the question bank, e.g.
    pct(male, total)
    perm + other
    div(total_energy, ref('P6.DEN', 'revenue'))
They are parsed with Python's ``ast`` module and evaluated by walking a
whitelist of node types. Nothing is ever passed to ``eval``.

Missing-value semantics are deliberate and uniform:
- arithmetic with a missing operand yields None (the cell renders as
  blank, never as a misleading 0);
- ``sum``/``add`` ignore missing operands but return None if *all* are
  missing;
- division by zero or by a missing denominator yields None.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from typing import Any, Callable, Optional

Number = Optional[float]


class FormulaError(ValueError):
    """Raised at question-bank load time for a malformed formula, so a
    typo in the bank fails the test suite instead of a client's report."""


def _num(v: Any) -> Number:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _pct(a: Any, b: Any) -> Number:
    a, b = _num(a), _num(b)
    if a is None or b is None or b == 0:
        return None
    return a / b * 100.0


def _div(a: Any, b: Any, scale: Any = 1) -> Number:
    a, b, scale = _num(a), _num(b), _num(scale)
    if a is None or b is None or b == 0 or scale is None:
        return None
    return a / b * scale


def _sum(*args: Any) -> Number:
    vals = [_num(a) for a in args]
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


_FUNCS: dict[str, Callable[..., Number]] = {
    "pct": _pct,
    "div": _div,
    "sum": _sum,
    "add": _sum,
}

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


@lru_cache(maxsize=2048)
def parse(expr: str) -> ast.Expression:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - exercised via validate()
        raise FormulaError(f"Invalid formula {expr!r}: {exc}") from exc
    _check(tree.body, expr)
    return tree


def _check(node: ast.AST, expr: str) -> None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, str)):
            return
        raise FormulaError(f"Unsupported constant in {expr!r}")
    if isinstance(node, ast.Name):
        return
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        _check(node.left, expr)
        _check(node.right, expr)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        _check(node.operand, expr)
        return
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id not in _FUNCS and node.func.id != "ref":
            raise FormulaError(f"Unknown function {node.func.id!r} in {expr!r}")
        if node.keywords:
            raise FormulaError(f"Keyword arguments are not allowed in {expr!r}")
        for a in node.args:
            _check(a, expr)
        return
    raise FormulaError(f"Unsupported syntax {type(node).__name__} in {expr!r}")


def names_in(expr: str) -> set[str]:
    """Variable names a formula reads (excluding function names and the
    string arguments of ref()). Used by the bank self-check."""
    tree = parse(expr)
    out: set[str] = set()

    def walk(n: ast.AST) -> None:
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name) and n.func.id == "ref":
                return
            for a in n.args:
                walk(a)
            return
        if isinstance(n, ast.Name):
            out.add(n.id)
            return
        for child in ast.iter_child_nodes(n):
            walk(child)

    walk(tree.body)
    return out


def refs_in(expr: str) -> list[tuple[str, str]]:
    """(question_id, row_key) pairs referenced via ref(...)."""
    tree = parse(expr)
    out: list[tuple[str, str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "ref":
            if len(n.args) != 2 or not all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in n.args):
                raise FormulaError(f"ref() takes two string literals in {expr!r}")
            out.append((n.args[0].value, n.args[1].value))
    return out


def evaluate(expr: str, lookup: Callable[[str], Any], ref: Callable[[str, str], Any] | None = None) -> Number:
    """Evaluate ``expr``. ``lookup(name)`` resolves a variable; ``ref(qid,
    row)`` resolves a value from another question in the same column."""
    tree = parse(expr)

    def ev(n: ast.AST) -> Any:
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return _num(lookup(n.id))
        if isinstance(n, ast.UnaryOp):
            v = _num(ev(n.operand))
            return None if v is None else -v
        if isinstance(n, ast.BinOp):
            a, b = _num(ev(n.left)), _num(ev(n.right))
            if isinstance(n.op, ast.Add):
                # Totals: a missing part does not erase the parts that exist.
                return _sum(a, b)
            if a is None or b is None:
                return None
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if isinstance(n.op, ast.Div):
                return None if b == 0 else a / b
        if isinstance(n, ast.Call):
            fname = n.func.id  # type: ignore[attr-defined]
            if fname == "ref":
                if ref is None:
                    return None
                return _num(ref(n.args[0].value, n.args[1].value))  # type: ignore[attr-defined]
            return _FUNCS[fname](*[ev(a) for a in n.args])
        raise FormulaError(f"Unsupported node {type(n).__name__}")  # pragma: no cover

    return _num(ev(tree.body))
