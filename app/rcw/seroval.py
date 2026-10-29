"""Reads the listing data Imoova's web pages embed for the browser (2026-10-29).

Imoova's API host (api.imoova.com) has a robots.txt that disallows everything, so the watcher reads the public
www pages instead, which robots.txt allows. Those pages carry their data as JavaScript object literals
(TanStack Router's seroval format), not JSON: unquoted keys, !0 / !1 for true / false, and $R[n]= labels.
Only flat objects are needed, so this reads one object's simple fields and skips anything nested.
"""
import json
import re

_KEY = re.compile(r"\s*([A-Za-z_$][\w$]*)\s*:")
_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_REF = re.compile(r"\$R\[\d+\]")
_HEX = re.compile(r"(?<!\\)((?:\\\\)*)\\x([0-9A-Fa-f]{2})")


def _string(text: str, i: int) -> tuple[str, int]:
    """text[i] is an opening quote -> (decoded string, index after the closing quote)."""
    j = i + 1
    while True:
        k = text.index('"', j)
        n = 0
        while text[k - 1 - n] == "\\":
            n += 1
        if n % 2 == 0:
            break
        j = k + 1
    raw = _HEX.sub(r"\1\\u00\2", text[i:k + 1])  # JS \x3C -> JSON <
    return json.loads(raw), k + 1


def _skip(text: str, i: int) -> int:
    """Skip a nested value starting at text[i] -> index just after it."""
    depth = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            _, i = _string(text, i)
            continue
        if c in "{[(":
            depth += 1
        elif c in "}])":
            if depth == 0:
                return i  # end of the enclosing object: the value had no brackets
            depth -= 1
            if depth == 0:
                return i + 1
        elif c == "," and depth == 0:
            return i
        i += 1
    raise ValueError("unterminated value")


def read_object(text: str, start: int) -> tuple[dict, int]:
    """text[start] == '{' -> (the object's simple fields, index after its closing brace)."""
    out: dict = {}
    i = start + 1
    while i < len(text):
        while text[i] in " \t\r\n,":
            i += 1
        if text[i] == "}":
            return out, i + 1
        m = _KEY.match(text, i)
        if not m:
            raise ValueError(f"unexpected text at {i}: {text[i:i + 30]!r}")
        key, i = m.group(1), m.end()
        r = _REF.match(text, i)
        if r:
            i = r.end()
            if text[i] != "=":
                continue  # a back-reference to something already sent: not a value we need
            i += 1
        c = text[i]
        if c == '"':
            out[key], i = _string(text, i)
        elif c in "{[":
            i = _skip(text, i)
        elif text.startswith("!0", i):
            out[key], i = True, i + 2
        elif text.startswith("!1", i):
            out[key], i = False, i + 2
        elif text.startswith("null", i):
            out[key], i = None, i + 4
        elif text.startswith("void 0", i):
            out[key], i = None, i + 6
        else:
            m = _NUM.match(text, i)
            if m and text[m.end()] in ",}":
                s = m.group(0)
                out[key] = float(s) if any(ch in s for ch in ".eE") else int(s)
                i = m.end()
            else:
                out[key] = None  # something exotic (new Date(...) and the like)
                i = _skip(text, i)
    raise ValueError("unterminated object")


def objects(text: str, typename: str) -> list[dict]:
    """Every object in the page whose __typename is typename (simple fields only)."""
    marker = '{__typename:"%s"' % typename
    out = []
    i = text.find(marker)
    while i >= 0:
        obj, end = read_object(text, i)
        out.append(obj)
        i = text.find(marker, end)
    return out
