"""Round-trip and sanity tests for the local obfuscation engine.

Runs with the standard library only (no Lua interpreter needed): the
decoding steps are re-implemented in Python against the structure of the
generated Lua. If `luaparser` is installed, the generated output is
additionally syntax-checked as Lua 5.1.

Usage:  python3 test_obfuscator.py
"""
import re
import string
import sys
import time

from local_obfuscator import obfuscate_lua

# A Lua table made of decimal-escaped string literals:  local _x={"\065\066",...}
_STR_TABLE = re.compile(r'local (_[A-Za-z0-9_]+)={\s*((?:"(?:\\\d{3})+"\s*,?\s*)+)}')
# A Lua table of bare integers:  local _x={3,1,2,...}
_INT_TABLE = re.compile(r'local (_[A-Za-z0-9_]+)={([\d,]+)}')
# A line that is exactly:  local _x=123
_BARE_INT = re.compile(r'^local (_[A-Za-z0-9_]+)=(\d+)\s*$', re.M)

try:
    from luaparser import ast as _lua_ast
except ImportError:
    _lua_ast = None


def _bytes_from_escaped(s: str) -> bytes:
    return bytes(int(m) for m in re.findall(r'\\(\d{3})', s))


def _decode_outer(text: str) -> str:
    """Outer loader -> layer-2 decoder source (byte-shift reversal)."""
    m = _STR_TABLE.search(text)
    assert m, "outer payload table not found"
    blob = _bytes_from_escaped(m.group(2))
    keys = _BARE_INT.findall(text)
    assert keys, "shift key not found in outer loader"
    shift = int(keys[0][1])  # first bare int literal is the shift key
    return bytes((b + 512 - shift) % 256 for b in blob).decode("utf-8")


def _decode_l2(text: str) -> str:
    """Layer-2 decoder -> layer-1 decoder source (reverse + XOR)."""
    m = _STR_TABLE.search(text)
    assert m, "layer-2 payload table not found"
    payload = _bytes_from_escaped(m.group(2))
    keys = _BARE_INT.findall(text)
    assert len(keys) == 1, f"expected exactly one bare int key in L2 decoder, got {keys}"
    xk = int(keys[0][1])
    return bytes(b ^ xk for b in reversed(payload)).decode("utf-8")


def _decode_l1(text: str) -> bytes:
    """Layer-1 decoder -> original source (unpermute + shift + XOR)."""
    m = _STR_TABLE.search(text)
    assert m, "layer-1 payload table not found"
    joined = _bytes_from_escaped(m.group(2))
    pm = _INT_TABLE.search(text)
    assert pm, "permutation table not found"
    perm = [int(x) for x in pm.group(2).split(",")]
    keys = _BARE_INT.findall(text)  # generation order: xor_key, shift_key, checksum
    assert len(keys) >= 2, f"expected xor/shift keys, got {keys}"
    xk, sk = int(keys[0][1]), int(keys[1][1])
    n = len(joined)
    assert len(perm) == n, f"perm length {len(perm)} != payload length {n}"
    out = bytearray(n)
    for i in range(n):
        idx = perm[i]
        assert 1 <= idx <= n, f"perm entry out of 1-based range: {idx}"
        out[idx - 1] = (joined[i] + 512 - sk) % 256
    return bytes(b ^ xk for b in out)


def roundtrip(code: str, label: str) -> int:
    t0 = time.time()
    obf = obfuscate_lua(code, f"{label}.lua")
    dt = time.time() - t0

    # No bare `~` (bitwise XOR) operator may appear anywhere - not in the
    # outer loader and not in the decoded decoder layers either. It is a
    # syntax error in Lua 5.1 / Luau. `~=` (not-equal) is fine.
    assert not re.search(r'~(?!=)', obf), f"{label}: bare `~` operator in output"

    l2_decoder = _decode_outer(obf)
    assert not re.search(r'~(?!=)', l2_decoder), f"{label}: bare `~` in L2 decoder"
    l1_decoder = _decode_l2(l2_decoder)
    assert not re.search(r'~(?!=)', l1_decoder), f"{label}: bare `~` in L1 decoder"

    # The decoder layers are compiled at runtime, so they must be valid Lua
    # too (this is where token-concatenation bugs hide).
    if _lua_ast is not None:
        _lua_ast.parse(obf)
        _lua_ast.parse(l2_decoder)
        _lua_ast.parse(l1_decoder)

    restored = _decode_l1(l1_decoder)
    assert restored.decode("utf-8") == code, f"{label}: round-trip mismatch"
    print(f"  ok  {label:<28} in={len(code):>8,}B  out={len(obf):>9,}B  {dt * 1000:7.0f}ms")
    return len(obf)


def main() -> int:
    print("Round-trip tests (Python-side decoder simulation):")

    roundtrip(
        'print("hello luraph")\n',
        "tiny",
    )

    # Real UTF-8 chars in the literal (é, →) plus tricky ASCII. Lua 5.1
    # has no \uNN escapes, so keep the sample valid on every target.
    special = (
        'local x = "h\u00e9llo \u2192 100% \\"quoted\\" \\n tab\\there"\n'
        "local t = {a=1, b={2,3}}\n"
        "for i=1,10 do\n"
        "  local s = string.rep('~', i)\n"
        "  print(s, i, t.a)\n"
        "end\n"
        "return t\n"
    )
    roundtrip(special, "special-chars")

    noise = (
        string.digits + string.ascii_letters + string.punctuation + "\n\t \u00e9"
    )
    big = "".join(noise[i % len(noise)] for i in range(100_000))
    roundtrip(big, "100KB-noise")

    # Optional Lua 5.1 syntax validation of a fresh output (round-trips
    # above already parse outer + both decoder layers per sample).
    if _lua_ast is None:
        print("\nluaparser not installed - skipping Lua 5.1 syntax validation.")
        return 0

    print("\nLua 5.1 syntax validation (luaparser):")
    sample = 'local ok = true\nprint("hi", ok)\nreturn ok\n'
    obf = obfuscate_lua(sample, "syntax.lua")
    _lua_ast.parse(obf)
    print("  ok  generated output parses as Lua 5.1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
