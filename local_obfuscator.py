"""Pure-Python Lua obfuscator — instant, no executor, no `lua` binary needed.

Strategy: the entire source file is encoded (byte-shift + decimal escapes)
and embedded as a payload. The emitted chunk decodes it at runtime and
executes it via ``loadstring``/``load``. This is:

* Fast — runs in milliseconds on Render, no queue / executor wait.
* Semantics-preserving — the original code runs exactly as-is inside the
  decoded chunk (return values and ``...`` varargs are forwarded, so both
  Scripts and ModuleScripts keep working).
* Compatible — the loader only uses ``string`` / ``table`` / ``loadstring``,
  which exist in stock Lua 5.1+ and Roblox Luau executors.

Note: like any ``loadstring``-based loader, the output needs ``loadstring``
(or ``load``) enabled where it runs. Executor environments normally have it.
"""

from __future__ import annotations

import random
import string as _string
import time

LUA_KEYWORDS = frozenset({
    "and", "break", "do", "else", "elseif", "end", "false", "for",
    "function", "goto", "if", "in", "local", "nil", "not", "or",
    "repeat", "return", "then", "true", "until", "while",
    # Luau extras
    "continue", "type", "export", "self",
})

_IDENT_CHARS = _string.ascii_letters + _string.digits + "_"

# How many raw bytes go into each embedded string chunk. Small chunks keep
# lines reasonably short and make the payload harder to eyeball.
CHUNK_BYTES = 1024


def _rand_ident(rng: random.Random, taken: set[str]) -> str:
    """Generate a random Lua identifier like ``_x7QaZ`` (never a keyword)."""
    while True:
        length = rng.randint(5, 10)
        name = "_" + "".join(rng.choice(_IDENT_CHARS) for _ in range(length))
        if name not in taken and name not in LUA_KEYWORDS:
            taken.add(name)
            return name


def _escape_bytes(data: bytes) -> str:
    """Encode bytes as Lua decimal escapes: b'AB' -> '\\\\065\\\\066'."""
    return "".join(f"\\{b:03d}" for b in data)


def _junk_comment(rng: random.Random) -> str:
    return "-- %s" % "".join(rng.choice("0123456789abcdef") for _ in range(12))


def obfuscate_lua(code: str, source_name: str = "script") -> str:
    """Obfuscate Lua source code. Returns the obfuscated Lua chunk.

    Raises:
        ValueError: if the input is empty.
    """
    if not code or not code.strip():
        raise ValueError("Input file is empty.")

    rng = random.Random(random.randrange(1 << 60))
    taken: set[str] = set()

    # Randomize every local in the loader so each run looks different.
    v_parts = _rand_ident(rng, taken)   # table of payload chunks
    v_blob = _rand_ident(rng, taken)    # joined payload string
    v_key = _rand_ident(rng, taken)     # shift key
    v_out = _rand_ident(rng, taken)     # decoded char table
    v_idx = _rand_ident(rng, taken)     # loop index
    v_src = _rand_ident(rng, taken)     # decoded source
    v_load = _rand_ident(rng, taken)    # loadstring/load
    v_fn = _rand_ident(rng, taken)      # decoded function
    v_err = _rand_ident(rng, taken)     # load error

    raw = code.encode("utf-8")
    key = rng.randint(1, 255)
    shifted = bytes((b + key) % 256 for b in raw)

    chunks = [
        _escape_bytes(shifted[i:i + CHUNK_BYTES])
        for i in range(0, len(shifted), CHUNK_BYTES)
    ]

    # Payload as a table of string literals joined with table.concat —
    # efficient even for large files (no O(n^2) `..` chain).
    payload_lines = [f'"{c}"' for c in chunks]
    payload_table = ",\n".join(payload_lines)

    safe_name = "".join(
        ch for ch in (source_name or "script") if ch.isprintable()
    ).replace("\n", " ")[:80] or "script"

    header = (
        f"-- Obfuscated by Luraph-Obfuscator-bot | "
        f"original: {safe_name} | {len(raw)} bytes | {time.strftime('%Y-%m-%d')}"
    )

    out: list[str] = [header, _junk_comment(rng)]
    out.append(f"local {v_parts}={{")
    out.append(payload_table)
    out.append("}")
    out.append(_junk_comment(rng))
    out.append(f"local {v_blob}=table.concat({v_parts})")
    out.append(f"local {v_key}={key}")
    out.append(f"local {v_out}={{}}")
    # NOTE: parens around the subtraction are required — % binds tighter than -.
    out.append(
        f"for {v_idx}=1,#{v_blob} do "
        f"{v_out}[{v_idx}]=string.char(({v_blob}:byte({v_idx})-{v_key})%256) end"
    )
    out.append(f"local {v_src}=table.concat({v_out})")
    out.append(_junk_comment(rng))
    out.append(f"local {v_load}=loadstring or load")
    out.append(f"local {v_fn},{v_err}={v_load}({v_src},\"=(obfuscated)\")")
    out.append(
        f"if not {v_fn} then error(\"Failed to load chunk: \"..tostring({v_err})) end"
    )
    out.append(f"return {v_fn}(...)")
    out.append("")  # trailing newline
    return "\n".join(out)


def deobfuscate_payload(obfuscated: str) -> str:
    """Best-effort decode of our own payload (used by tests only)."""
    import re

    key_m = re.search(r"local _[A-Za-z0-9_]+?=(\d{1,3})\n", obfuscated)
    if not key_m:
        raise ValueError("key not found")
    key = int(key_m.group(1))
    escapes = re.findall(r'"((?:\\\d{3})+)"', obfuscated)
    if not escapes:
        raise ValueError("payload not found")
    raw = b"".join(
        bytes(int(chunk[i + 1:i + 4]) for i in range(0, len(chunk), 4))
        for chunk in escapes
    )
    return bytes((b - key) % 256 for b in raw).decode("utf-8")
