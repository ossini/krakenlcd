# SPDX-License-Identifier: GPL-3.0-or-later
"""The `{{ … }}` placeholders in a design.

A design is an ordinary SVG file with expressions in double braces wherever
something should change: `{{ cpu }}` in a text element, `{{ arc(160, 160,
148, -135, map(cpu, 30, 100, -135, 135)) }}` in a path, `{{ '#d8342a' if
cpu >= 80 else '#111214' }}` in a fill.  The file is substituted as text and
only then handed to the SVG renderer, so a placeholder can go anywhere -- an
attribute, a text node, a stylesheet.

The expressions look like Python but are not run as Python.  They are parsed
with `ast`, and only a small, harmless subset is accepted: numbers and
strings, arithmetic, comparisons, `and`/`or`/`not`, `a if c else b`, and
calls to the functions in FUNCTIONS.  No attributes, no indexing, no imports,
nothing that reaches outside -- designs get passed around, and opening one
must never be able to do more than draw a picture.

A sensor that is not there (no GPU, no hwmon driver) is MISSING rather than
an error.  MISSING is falsy, compares unequal to everything, swallows
arithmetic and prints as "--", so `{{ cpu }}` on a machine without the sensor
reads "--" and `{{ cpu or 0 }}` gives a design a number to fall back on.
"""

from __future__ import annotations

import ast
import difflib
import html
import inspect
import math
import operator
import re
import time
from xml.sax.saxutils import escape

__all__ = ["Template", "TemplateError", "MISSING", "FUNCTIONS", "to_text"]


class TemplateError(ValueError):
    """A placeholder that cannot be parsed or evaluated."""

    def __init__(self, message: str, line: int | None = None,
                 source: str | None = None):
        self.message = message
        self.line = line
        self.source = source
        where = f"line {line}: " if line else ""
        code = f"  in {{{{{source.strip()}}}}}" if source else ""
        super().__init__(f"{where}{message}{code}")


class _Missing:
    """A reading that is not available.  See the module docstring."""

    __slots__ = ()

    def __bool__(self):
        return False

    def __repr__(self):
        return "MISSING"

    def __str__(self):
        return "--"


MISSING = _Missing()


# ---------------------------------------------------------------------------
# output

def to_text(value) -> str:
    """How a value looks once it is in the SVG."""
    if value is MISSING or value is None:
        return "--"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "--"
        # Three decimals is a thousandth of a pixel: plenty for geometry, and
        # stable, so the same reading always yields the same text.
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return "0" if text in ("", "-0") else text
    return str(value)


def _xml(text: str) -> str:
    return escape(text, {'"': "&quot;", "'": "&apos;"})


# ---------------------------------------------------------------------------
# functions a design may call

def _missing(*values) -> bool:
    return any(v is MISSING or v is None for v in values)


def _number(value, what: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{what} needs a number, got {to_text(value)!r}")
    return value


def f_map(value, in_lo, in_hi, out_lo, out_hi):
    """`value` from in_lo..in_hi onto out_lo..out_hi, clamped at both ends."""
    if _missing(value, in_lo, in_hi, out_lo, out_hi):
        return MISSING
    for v in (value, in_lo, in_hi, out_lo, out_hi):
        _number(v, "map()")
    if in_hi == in_lo:
        return out_lo
    t = max(0.0, min(1.0, (value - in_lo) / (in_hi - in_lo)))
    return out_lo + (out_hi - out_lo) * t


def f_clamp(value, lo, hi):
    if _missing(value, lo, hi):
        return MISSING
    return max(_number(lo, "clamp()"),
               min(_number(hi, "clamp()"), _number(value, "clamp()")))


def f_round(value, decimals=0):
    if _missing(value):
        return MISSING
    decimals = int(_number(decimals, "round()"))
    result = round(_number(value, "round()"), max(0, min(6, decimals)))
    return int(result) if decimals <= 0 else result


def f_fmt(value, decimals=0):
    """A number as text with exactly `decimals` places; "--" if missing."""
    if _missing(value):
        return "--"
    decimals = max(0, min(6, int(_number(decimals, "fmt()"))))
    text = f"{_number(value, 'fmt()'):.{decimals}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text


def f_min(*values):
    if not values or _missing(*values):
        return MISSING
    return min(_number(v, "min()") for v in values)


def f_max(*values):
    if not values or _missing(*values):
        return MISSING
    return max(_number(v, "max()") for v in values)


def f_abs(value):
    return MISSING if _missing(value) else abs(_number(value, "abs()"))


def _point(cx, cy, r, angle):
    rad = math.radians(angle)
    return to_text(cx + r * math.sin(rad)), to_text(cy - r * math.cos(rad))


def f_arc(cx, cy, r, start, end):
    """SVG path data for a circular arc.

    Angles are compass degrees: 0 is twelve o'clock and they grow clockwise,
    so a gauge running from 7:30 round to 4:30 is `arc(cx, cy, r, -135,
    135)`.  An arc of no length is the empty string, which draws nothing.
    """
    if _missing(cx, cy, r, start, end):
        return ""
    for v in (cx, cy, r, start, end):
        _number(v, "arc()")
    if end < start:
        start, end = end, start
    sweep = end - start
    if sweep < 1e-6 or r <= 0:
        return ""
    rr = to_text(r)
    if sweep >= 360:
        # One arc cannot end where it starts; a full ring is two halves.
        x0, y0 = _point(cx, cy, r, start)
        x1, y1 = _point(cx, cy, r, start + 180)
        return (f"M{x0} {y0} A{rr} {rr} 0 1 1 {x1} {y1} "
                f"A{rr} {rr} 0 1 1 {x0} {y0} Z")
    x0, y0 = _point(cx, cy, r, start)
    x1, y1 = _point(cx, cy, r, end)
    large = 1 if sweep > 180 else 0
    return f"M{x0} {y0} A{rr} {rr} 0 {large} 1 {x1} {y1}"


_HEX = re.compile(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


def _rgb(color):
    m = _HEX.fullmatch(str(color).strip())
    if not m:
        raise ValueError(f"colours are written like '#ff8800', not "
                         f"{color!r}")
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def f_mix(color_a, color_b, t):
    """Blend two '#rrggbb' colours; t = 0 is the first, 1 the second."""
    a, b = _rgb(color_a), _rgb(color_b)
    if _missing(t):
        return "#%02x%02x%02x" % a
    t = max(0.0, min(1.0, float(_number(t, "mix()"))))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def f_ramp(t, *colors):
    """A colour along a list of them: t = 0 is the first, 1 the last.

    Blending two far-apart colours straight goes through grey -- cyan to red
    is mud in the middle.  A stop in between (cyan, yellow, red) fixes that.
    """
    if len(colors) < 2:
        raise ValueError("ramp() needs a position and at least two colours")
    if _missing(t):
        return f_mix(colors[0], colors[0], 0)
    t = max(0.0, min(1.0, float(_number(t, "ramp()"))))
    position = t * (len(colors) - 1)
    i = min(int(position), len(colors) - 2)
    return f_mix(colors[i], colors[i + 1], position - i)


def f_upper(text):
    return to_text(text).upper()


def f_lower(text):
    return to_text(text).lower()


def f_len(text):
    return len(to_text(text))


def f_now(fmt="%H:%M"):
    """The local time, formatted with strftime codes."""
    fmt = to_text(fmt)
    if len(fmt) > 64:
        raise ValueError("now() format is too long")
    return time.strftime(fmt)


FUNCTIONS = {
    "map": f_map,
    "clamp": f_clamp,
    "round": f_round,
    "fmt": f_fmt,
    "min": f_min,
    "max": f_max,
    "abs": f_abs,
    "arc": f_arc,
    "mix": f_mix,
    "ramp": f_ramp,
    "upper": f_upper,
    "lower": f_lower,
    "len": f_len,
    "now": f_now,
}


# ---------------------------------------------------------------------------
# the expression compiler

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: None,
}
_CMPOPS = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
    ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne,
}
_SYMBOLS = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
    ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**",
}


def _binop(op, a, b):
    if _missing(a, b):
        return MISSING
    if op is ast.Add and (isinstance(a, str) or isinstance(b, str)):
        return to_text(a) + to_text(b)      # 'GPU ' + gpu just works
    _number(a, f"'{_SYMBOLS[op]}'")
    _number(b, f"'{_SYMBOLS[op]}'")
    if op is ast.Pow:
        # Through floats, so 9 ** 9 ** 9 is an OverflowError and not an
        # attempt to build a number with a few hundred million digits.
        result = math.pow(a, b)
        return int(result) if result.is_integer() and abs(result) < 2 ** 53 \
            else result
    return _BINOPS[op](a, b)


def _compare(op, a, b):
    if _missing(a) or _missing(b):
        return op is ast.NotEq
    if isinstance(a, str) != isinstance(b, str) and op not in (ast.Eq,
                                                               ast.NotEq):
        raise TypeError(f"cannot compare {to_text(a)!r} with {to_text(b)!r}")
    return _CMPOPS[op](a, b)


class _Compiler:
    """Turns a parsed expression into nested closures, checking as it goes."""

    def __init__(self, functions):
        self.functions = functions
        self.names: set[str] = set()

    def compile(self, node):
        method = getattr(self, "_" + type(node).__name__, None)
        if method is None:
            raise TemplateError(f"'{_describe(node)}' is not allowed here")
        return method(node)

    def _Expression(self, node):
        return self.compile(node.body)

    def _Constant(self, node):
        value = node.value
        if value is None:
            return lambda env: MISSING
        if not isinstance(value, (bool, int, float, str)):
            raise TemplateError(f"{value!r} is not allowed here")
        return lambda env: value

    def _Name(self, node):
        name = node.id
        if name in ("true", "True"):
            return lambda env: True
        if name in ("false", "False"):
            return lambda env: False
        self.names.add(name)
        return lambda env: env(name)

    def _UnaryOp(self, node):
        operand = self.compile(node.operand)
        if isinstance(node.op, ast.Not):
            return lambda env: not operand(env)
        if isinstance(node.op, ast.USub):
            def neg(env):
                v = operand(env)
                return MISSING if _missing(v) else -_number(v, "'-'")
            return neg
        if isinstance(node.op, ast.UAdd):
            return operand
        raise TemplateError(f"'{_describe(node)}' is not allowed here")

    def _BinOp(self, node):
        op = type(node.op)
        if op not in _BINOPS:
            raise TemplateError(f"'{_describe(node)}' is not allowed here")
        left, right = self.compile(node.left), self.compile(node.right)
        return lambda env: _binop(op, left(env), right(env))

    def _BoolOp(self, node):
        values = [self.compile(v) for v in node.values]
        if isinstance(node.op, ast.And):
            def all_of(env):
                result = True
                for v in values:
                    result = v(env)
                    if not result:
                        return result
                return result
            return all_of

        def any_of(env):
            result = False
            for v in values:
                result = v(env)
                if result:
                    return result
            return result
        return any_of

    def _Compare(self, node):
        ops = [type(op) for op in node.ops]
        for op in ops:
            if op not in _CMPOPS:
                raise TemplateError(f"'{_describe(node)}' is not allowed here")
        left = self.compile(node.left)
        rights = [self.compile(c) for c in node.comparators]

        def compare(env):
            a = left(env)
            for op, right in zip(ops, rights):
                b = right(env)
                if not _compare(op, a, b):
                    return False
                a = b
            return True
        return compare

    def _IfExp(self, node):
        test = self.compile(node.test)
        body, orelse = self.compile(node.body), self.compile(node.orelse)
        return lambda env: body(env) if test(env) else orelse(env)

    def _Call(self, node):
        if not isinstance(node.func, ast.Name):
            raise TemplateError(f"'{_describe(node.func)}' is not a function "
                                f"you can call here")
        name = node.func.id
        if name not in self.functions:
            raise TemplateError(f"there is no function called {name}()"
                                + _suggest(name, self.functions))
        function = self.functions[name]
        usage = f"{name}{_signature(function)}"
        if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
            raise TemplateError(f"write the values in order: {usage}")
        try:
            inspect.signature(function).bind(*node.args)
        except TypeError:
            raise TemplateError(f"{name}() does not take {len(node.args)} "
                                f"value{'s' if len(node.args) != 1 else ''}"
                                f": {usage}") from None
        args = [self.compile(a) for a in node.args]
        return lambda env: env.call(name, [a(env) for a in args])


def _signature(function) -> str:
    return str(inspect.signature(function)).replace("'", "")


def _describe(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return type(node).__name__


def _suggest(word, choices) -> str:
    close = difflib.get_close_matches(word, list(choices), n=1)
    return f" -- did you mean {close[0]}?" if close else ""


class _Expr:
    __slots__ = ("source", "line", "run", "names")

    def __init__(self, source, line, run, names):
        self.source = source
        self.line = line
        self.run = run
        self.names = names


class _Env:
    __slots__ = ("lookup", "functions")

    def __init__(self, lookup, functions):
        self.lookup = lookup
        self.functions = functions

    def __call__(self, name):
        return self.lookup(name)

    def call(self, name, args):
        return self.functions[name](*args)


# Comments are left alone: a note in a design that shows `{{ cpu }}` as an
# example should stay an example.
_TOKENS = re.compile(r"(<!--.*?-->)|\{\{(.*?)\}\}", re.DOTALL)


class Template:
    """A design file split into literal text and compiled placeholders."""

    def __init__(self, text: str, functions=None):
        self.functions = dict(FUNCTIONS if functions is None else functions)
        self.parts: list = []
        self.names: set[str] = set()
        pos = 0
        for m in _TOKENS.finditer(text):
            if m.group(1) is not None:
                continue
            self._literal(text, pos, m.start())
            line = text.count("\n", 0, m.start()) + 1
            self.parts.append(self._compile(m.group(2), line))
            pos = m.end()
        self._literal(text, pos, len(text))

    def _literal(self, text: str, start: int, end: int):
        chunk = text[start:end]
        # A `{{` that never closes is almost always a placeholder being typed;
        # passing it through as text would put it on the display verbatim.
        bare = re.sub(r"<!--.*?-->", lambda m: " " * len(m.group(0)), chunk,
                      flags=re.DOTALL)
        stray = bare.find("{{")
        if stray >= 0:
            line = text.count("\n", 0, start + stray) + 1
            raise TemplateError("'{{' is never closed with '}}'", line)
        self.parts.append(chunk)

    @property
    def expressions(self):
        return [p for p in self.parts if isinstance(p, _Expr)]

    def _compile(self, source: str, line: int) -> _Expr:
        # Editors write `>` and `&` in attributes as entities; the expression
        # wants them back.  Line breaks inside a placeholder are just spaces.
        code = html.unescape(source).replace("\n", " ").strip()
        if not code:
            raise TemplateError("empty placeholder", line, source)
        try:
            tree = ast.parse(code, mode="eval")
        except SyntaxError as exc:
            raise TemplateError(f"cannot read this expression ({exc.msg})",
                                line, source) from None
        compiler = _Compiler(self.functions)
        try:
            run = compiler.compile(tree)
        except TemplateError as exc:
            raise TemplateError(exc.message, line, source) from None
        except RecursionError:
            raise TemplateError("this expression is nested too deeply", line,
                                source) from None
        self.names |= compiler.names
        return _Expr(source, line, run, frozenset(compiler.names))

    def render(self, lookup, functions=None, on_error=None) -> str:
        """Fill in every placeholder.

        `lookup(name)` returns a variable's value.  `functions` overrides or
        extends the callable functions for this render.  An expression that
        fails at run time -- a division by zero, text where a number belongs
        -- is reported to `on_error(TemplateError)` and drawn as "--";
        without `on_error` it raises.
        """
        env = _Env(lookup, functions or self.functions)
        out = []
        for part in self.parts:
            if isinstance(part, str):
                out.append(part)
                continue
            try:
                value = part.run(env)
            except (TypeError, ValueError, ArithmeticError,
                    RecursionError) as exc:
                error = TemplateError(str(exc), part.line, part.source)
                if on_error is None:
                    raise error from None
                on_error(error)
                value = MISSING
            out.append(_xml(to_text(value)))
        return "".join(out)
