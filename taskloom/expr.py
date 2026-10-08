"""A small, safe expression language for parameters and conditions.

Expressions are parsed with Python's grammar but evaluated by walking an allow-listed
subset of the syntax tree: no attribute access, imports, lambdas or comprehensions, and
only the functions in the given namespace can be called.
"""

from __future__ import annotations

import ast
import calendar as _cal
import datetime as dt
import operator

from .calendars import Calendar

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}
_COMPARE = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b, ast.Is: operator.is_, ast.IsNot: operator.is_not,
}


class ExpressionError(ValueError):
    pass


def evaluate(source: str, names: dict):
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"invalid expression {source!r}: {e.msg}") from None
    return _eval(tree.body, names, source)


def _eval(node, names, source):
    ev = lambda n: _eval(n, names, source)  # noqa: E731
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise ExpressionError(f"unknown name '{node.id}' in {source!r}")
        return names[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](ev(node.left), ev(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](ev(node.operand))
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for v in node.values:
                result = ev(v)
                if not result:
                    return result
            return result
        result = False
        for v in node.values:
            result = ev(v)
            if result:
                return result
        return result
    if isinstance(node, ast.Compare):
        left = ev(node.left)
        for op, right_node in zip(node.ops, node.comparators):
            right = ev(right_node)
            if not _COMPARE[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return ev(node.body) if ev(node.test) else ev(node.orelse)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        func = names.get(node.func.id)
        if not callable(func):
            raise ExpressionError(f"unknown function '{node.func.id}' in {source!r}")
        if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
            raise ExpressionError(f"* and ** arguments are not allowed in {source!r}")
        return func(*[ev(a) for a in node.args], **{k.arg: ev(k.value) for k in node.keywords})
    if isinstance(node, (ast.List, ast.Tuple)):
        items = [ev(e) for e in node.elts]
        return items if isinstance(node, ast.List) else tuple(items)
    if isinstance(node, ast.Dict):
        return {ev(k): ev(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Subscript):
        return ev(node.value)[ev(node.slice)]
    raise ExpressionError(f"unsupported syntax '{ast.unparse(node)}' in {source!r}")


class _Months:
    def __init__(self, n: int):
        self.n = n

    def _shift(self, day, n):
        month_index = day.month - 1 + n
        year, month = day.year + month_index // 12, month_index % 12 + 1
        return day.replace(year=year, month=month, day=min(day.day, _cal.monthrange(year, month)[1]))

    def __radd__(self, day):
        return self._shift(day, self.n)

    def __rsub__(self, day):
        return self._shift(day, -self.n)


class _BDays:
    def __init__(self, n: int, calendar: Calendar):
        self.n, self.calendar = n, calendar

    def __radd__(self, day):
        return self.calendar.shift(day, self.n)

    def __rsub__(self, day):
        return self.calendar.shift(day, -self.n)


def functions(now: dt.datetime, calendar: Calendar, calendars) -> dict:
    """The callable namespace for expressions.

    `now` is fixed per run so every block and retry sees the same values.
    `calendars(name)` resolves the optional `cal=` argument of business-day functions.
    """
    cal_of = lambda name: calendar if name is None else calendars(name)  # noqa: E731

    def start_of_week(d):
        return d - dt.timedelta(days=d.weekday())

    def business_days_between(a, b, cal=None):
        c = cal_of(cal)
        lo, hi, sign = (a, b, 1) if a <= b else (b, a, -1)
        days = (lo + dt.timedelta(days=i) for i in range((hi - lo).days))
        return sign * sum(1 for d in days if c.is_business_day(d))

    def parse_date(text, fmt="%Y-%m-%d"):
        return dt.datetime.strptime(str(text), fmt).date()

    return {
        "today": lambda: now.date(),
        "now": lambda: now,
        "days": lambda n: dt.timedelta(days=n),
        "weeks": lambda n: dt.timedelta(weeks=n),
        "months": _Months,
        "bdays": lambda n, cal=None: _BDays(n, cal_of(cal)),
        "start_of_month": lambda d: d.replace(day=1),
        "end_of_month": lambda d: d.replace(day=_cal.monthrange(d.year, d.month)[1]),
        "start_of_week": start_of_week,
        "is_business_day": lambda d, cal=None: cal_of(cal).is_business_day(d),
        "next_business_day": lambda d, cal=None: cal_of(cal).shift(d, 1),
        "previous_business_day": lambda d, cal=None: cal_of(cal).shift(d, -1),
        "business_days_between": business_days_between,
        "format": format,
        "parse_date": parse_date,
        "int": int, "float": float, "str": str, "len": len,
        "min": min, "max": max, "abs": abs, "round": round,
    }
