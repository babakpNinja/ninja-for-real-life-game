#!/usr/bin/env python3
"""Reading facts out of the game's JavaScript, without reading its prose (#233).

Four checks in this app derive something from JS source with a regex — which
states `poseFor` draws, which states `frameMotion` swings, what `POSE_FALLBACK`
falls back to, where the engine makes particles. They read the file as text
because the file is the author: restating any of it in Python would let the two
drift and still agree with themselves.

Text is not code, though. A `case "..."` body runs to the *next* case, so it
swallows the comment written above that next case, and #231 was exactly that:
the reasoning for the cheer's swing quotes the literal `swing: 0`, the parse
read the prose, and `float` arrived in the still-swung list carrying a test it
was never about. The other direction is the quiet one — a comment that makes a
state drop out removes a parametrised test and shows up as a green run with
fewer cases.

So every one of those regexes sees `code_only()` first, and the two ways of
bounding a region (a function's body, a `const` object literal) live here once
instead of being spelled slightly differently at each call site.

Known limit: `code_only` understands strings and template literals, not regular
expression literals — `/[/*]/` would be read as the start of a comment. The
game's JS contains no regex literals at all (`grep` for one before assuming);
if that changes, this is where it has to learn about them.
"""
from __future__ import annotations

import re

QUOTES = "\"'`"


def code_only(src: str) -> str:
    """`src` with every comment blanked out — spaces, keeping the newlines.

    Blanked rather than deleted so offsets and line numbers survive: a caller
    can search this and slice the original, and a report can still say line 40.
    Comments inside string literals stay (a URL is not a comment).
    """
    out: list[str] = []
    i, n = 0, len(src)
    quote: str | None = None
    while i < n:
        c = src[i]
        if quote is not None:
            out.append(c)
            if c == "\\" and i + 1 < n:      # \" does not end the string
                out.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
        elif c in QUOTES:
            quote = c
            out.append(c)
            i += 1
        elif src.startswith("//", i):
            end = src.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in src[i:end]))
            i = end
        else:
            out.append(c)
            i += 1
    return "".join(out)


def function_body(src: str, name: str) -> str:
    """The text of `function name(...) { ... }`, comments blanked.

    Bounded by the closing brace in the first column, which is where this
    codebase puts the end of a top-level function — anything nested is
    indented. Without a bound the read runs to end-of-file: `states()` was
    parsing 777 lines of a 999-line file and so reading `frameMotion`'s switch
    as well as `poseFor`'s, agreeing with the truth only because both switches
    happen to name the same four states (#233).
    """
    m = re.search(r"function " + re.escape(name) + r"\(.*?\n\}", code_only(src), re.S)
    if not m:
        raise ValueError(
            f"no `function {name}(` with a closing brace in the first column — either it "
            f"was renamed, or it is nested now, and whatever reads {name} is reading "
            "nothing rather than saying so")
    return m.group(0)


def object_block(src: str, name: str) -> str:
    """The text between the braces of `const name = { ... }`, comments blanked.

    `object_literal` reads flat `key: "value"` tables; this one is for tables
    whose entries are themselves objects (the themes in `audio.js`, each with a
    tempo and a written motif), where a non-greedy `\\{(.*?)\\};` stops at the
    first inner `}` and reports a table with one entry in it.

    Braces are matched by depth, skipping string literals, so a `{` inside a
    quoted string does not open a level that never closes.
    """
    code = code_only(src)
    m = re.search(r"const " + re.escape(name) + r"\s*=\s*\{", code)
    if not m:
        raise ValueError(
            f"no `const {name} = {{` in this source, so the table it holds was read as "
            "empty rather than reported as missing")
    depth, i, n = 0, m.end() - 1, len(code)
    quote: str | None = None
    while i < n:
        c = code[i]
        if quote is not None:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in QUOTES:
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return code[m.end():i]
        i += 1
    raise ValueError(f"`const {name} = {{` is never closed")


def object_literal(src: str, name: str) -> dict[str, str]:
    """`const name = { key: "value", ... };` as a dict, comments blanked.

    Only the `word: "word"` pairs, which is all either caller wants. A
    commented-out or merely discussed entry inside those braces is not one of
    them — `POSE_FALLBACK`'s comment names the fallback it deliberately does
    *not* use, and without the blanking that rejected entry would be read as a
    live one.
    """
    m = re.search(r"const " + re.escape(name) + r" = \{(.*?)\};", code_only(src), re.S)
    if not m:
        raise ValueError(
            f"no `const {name} = {{...}};` in this source, so the table it holds was read "
            "as empty rather than reported as missing")
    pairs = dict(re.findall(r'(\w+):\s*"(\w+)"', m.group(1)))
    if not pairs:
        raise ValueError(
            f"`const {name}` parsed to no entries at all — either every entry is written "
            "some other way now, or the braces this read are not the ones it wanted")
    return pairs
