"""A tiny, dependency-free YAML reader for the config subset this project uses.

The whole point of git-hooks-collection is that a hook can be dropped into any
repository and run with nothing but a stock Python interpreter. That rules out a
hard dependency on PyYAML for reading ``.githooks.yaml``. When PyYAML *is*
installed we use it (see ``_core.load_config``); otherwise this module parses the
subset of YAML the config actually uses:

    - nested mappings (indentation based)
    - block sequences of scalars ("- item")
    - scalars: int, float, bool, null, and quoted / unquoted strings
    - inline flow lists ("[a, b, c]")
    - "#" comments (respecting quotes)

It is deliberately small. It is not a general YAML implementation and does not
try to be. If you hand it something outside the supported subset it raises
``MiniYamlError`` rather than guessing.
"""

from __future__ import annotations

from typing import Any, List, Tuple

__all__ = ["safe_load", "MiniYamlError"]


class MiniYamlError(ValueError):
    """Raised when the input is outside the supported YAML subset."""


_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}
_NULL = {"", "null", "~"}


def _strip_comment(line: str) -> str:
    """Remove a trailing ``# comment`` while respecting quoted strings."""
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1] in " \t":
                return line[:i]
    return line


def _tokenize(text: str) -> List[Tuple[int, str]]:
    """Turn raw text into a list of ``(indent, content)`` for non-blank lines."""
    tokens: List[Tuple[int, str]] = []
    for raw in text.splitlines():
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise MiniYamlError("tabs are not allowed for indentation")
        without_comment = _strip_comment(raw)
        stripped = without_comment.strip()
        if not stripped:
            continue
        if stripped == "---":  # document start marker, ignore
            continue
        indent = len(without_comment) - len(without_comment.lstrip(" "))
        tokens.append((indent, stripped))
    return tokens


def _parse_scalar(token: str) -> Any:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    if token.startswith("[") and token.endswith("]"):
        return _parse_flow_list(token)
    low = token.lower()
    if low in _NULL:
        return None
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def _parse_flow_list(token: str) -> List[Any]:
    inner = token[1:-1].strip()
    if not inner:
        return []
    items: List[str] = []
    buf = ""
    in_single = False
    in_double = False
    for ch in inner:
        if ch == "'" and not in_double:
            in_single = not in_single
            buf += ch
        elif ch == '"' and not in_single:
            in_double = not in_double
            buf += ch
        elif ch == "," and not in_single and not in_double:
            items.append(buf)
            buf = ""
        else:
            buf += ch
    items.append(buf)
    return [_parse_scalar(part) for part in items if part.strip() != ""]


def _split_key_value(content: str) -> Tuple[str, str]:
    """Split ``key: value`` respecting quotes; returns (key, value_or_empty)."""
    in_single = False
    in_double = False
    for i, ch in enumerate(content):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            after = content[i + 1 :]
            if after == "" or after[0] in " \t":
                return content[:i].strip(), after.strip()
    if content.endswith(":"):
        return content[:-1].strip(), ""
    raise MiniYamlError(f"expected 'key: value' mapping, got: {content!r}")


class _Cursor:
    def __init__(self, tokens: List[Tuple[int, str]]):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def advance(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok


def _parse_block(cur: _Cursor, indent: int) -> Any:
    first = cur.peek()
    if first is None:
        return None
    if first[1].startswith("- ") or first[1] == "-":
        return _parse_sequence(cur, indent)
    return _parse_mapping(cur, indent)


def _parse_mapping(cur: _Cursor, indent: int) -> dict:
    result: dict = {}
    while True:
        tok = cur.peek()
        if tok is None or tok[0] != indent:
            break
        line_indent, content = tok
        if content.startswith("- "):
            raise MiniYamlError("unexpected sequence item inside mapping")
        cur.advance()
        key_raw, value_raw = _split_key_value(content)
        key = _parse_scalar(key_raw)
        if value_raw == "":
            nxt = cur.peek()
            if nxt is not None and nxt[0] > line_indent:
                result[key] = _parse_block(cur, nxt[0])
            else:
                result[key] = None
        else:
            result[key] = _parse_scalar(value_raw)
    return result


def _parse_sequence(cur: _Cursor, indent: int) -> list:
    result: list = []
    while True:
        tok = cur.peek()
        if tok is None or tok[0] != indent:
            break
        line_indent, content = tok
        if not (content.startswith("- ") or content == "-"):
            break
        cur.advance()
        item = content[1:].strip()
        if item == "":
            nxt = cur.peek()
            if nxt is not None and nxt[0] > line_indent:
                result.append(_parse_block(cur, nxt[0]))
            else:
                result.append(None)
        else:
            result.append(_parse_scalar(item))
    return result


def safe_load(text: str) -> Any:
    """Parse a YAML string from the supported subset and return Python data."""
    tokens = _tokenize(text)
    if not tokens:
        return None
    cur = _Cursor(tokens)
    base_indent = tokens[0][0]
    value = _parse_block(cur, base_indent)
    if cur.peek() is not None:
        raise MiniYamlError(
            f"could not parse line {cur.peek()[1]!r}; check indentation"
        )
    return value
