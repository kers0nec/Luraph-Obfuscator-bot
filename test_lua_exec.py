"""Execute obfuscated output in real Lua (via lupa) and compare behaviour
against the original source: same printed lines, same return value.

Run with a venv that has `lupa` installed (pip install lupa).
"""
import os
import string
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import lupa
except ImportError:
    sys.exit("This test needs lupa (pip install lupa) to execute Lua.")

from local_obfuscator import obfuscate_lua

SAMPLES = {
    "arithmetic": (
        "local total = 0\n"
        "for i=1,5 do total = total + i * 2 end\n"
        'print("sum", total)\n'
        "return total\n"
    ),
    "tables": (
        "local t = {1, 2, 3}\n"
        "t[#t + 1] = 10\n"
        "local s = 0\n"
        "for k, v in pairs(t) do s = s + v end\n"
        'print("count", #t, "sum", s)\n'
        "return s\n"
    ),
    # NOTE 1: samples must return/printf VALID UTF-8 — lupa converts Lua
    # strings to Python str and raises otherwise. (sub(1,3) keeps the
    # 2-byte é intact; sub(1,2) would cut it and break the harness.)
    # NOTE 2: use Python \u escapes so the Lua source contains real UTF-8
    # bytes (C3 A9) inside the literal — Python's \xNN creates the
    # *character* U+00NN, not a raw byte, which would smuggle mojibake
    # into the sample.
    "strings-unicode": (
        'local a = "h\u00e9llo \u2192 world"\n'
        'local b = a:sub(1, 3) .. "!"\n'
        "print(b, string.len(a))\n"
        "return b\n"
    ),
}


def run(lua, code: str):
    """Run a chunk; return (printed_lines, return_value).

    Lua strings that are not valid UTF-8 cannot be converted to Python str
    by lupa, so run_bytes() (hex dump) is the fallback in main().
    """
    out = []
    lua.globals()["print"] = lambda *args: out.append(
        " ".join(str(a) for a in args)
    )
    result = lua.execute(code)
    return out, result


_BYTE_DUMP = '''
local f = load(CHUNK, "=(X)")
if not f then error(LERR) end
local b = f()
if type(b) ~= "string" then return tostring(b) end
local s = ""
for i = 1, #b do s = s .. string.format("%02x ", b:byte(i)) end
return s
'''


def run_bytes(lua, code: str):
    """Run a chunk whose return value is a (possibly non-UTF-8) byte string;
    the return value is compared as a hex dump."""
    out = []
    lua.globals()["print"] = lambda *args: out.append(
        " ".join(str(a) for a in args)
    )
    lua.globals()["CHUNK"] = code
    lua.globals()["LERR"] = ""
    return out, lua.execute(_BYTE_DUMP)


def main() -> int:
    failures = 0
    for name, code in SAMPLES.items():
        for seed in range(5):
            obf = obfuscate_lua(code, f"{name}.lua")
            try:
                try:
                    orig_out, orig_ret = run(lupa.LuaRuntime(), code)
                    obf_out, obf_ret = run(lupa.LuaRuntime(), obf)
                except UnicodeDecodeError:
                    # Non-UTF-8 return value: compare raw byte dumps.
                    orig_out, orig_ret = run_bytes(lupa.LuaRuntime(), code)
                    obf_out, obf_ret = run_bytes(lupa.LuaRuntime(), obf)
            except Exception as e:
                print(f"  FAIL  {name} seed={seed}: {type(e).__name__}: {e}")
                failures += 1
                continue
            if orig_out != obf_out or orig_ret != obf_ret:
                print(
                    f"  FAIL  {name} seed={seed}: behaviour differs\n"
                    f"        orig: out={orig_out!r} ret={orig_ret!r}\n"
                    f"        obf:  out={obf_out!r} ret={obf_ret!r}"
                )
                failures += 1
                continue
            print(f"  ok    {name} seed={seed}  ret={orig_ret!r}")

    # 100KB round-trip through real Lua
    noise = string.digits + string.ascii_letters + "\n\t"
    big = "".join(noise[i % len(noise)] for i in range(100_000))
    big_code = "local data = [[\n" + big + "\n]]\nprint(#data)\nreturn #data\n"
    try:
        _, orig_ret = run(lupa.LuaRuntime(), big_code)
        obf = obfuscate_lua(big_code, "big.lua")
        _, obf_ret = run(lupa.LuaRuntime(), obf)
    except Exception as e:
        print(f"  FAIL  100KB-noise: {type(e).__name__}: {e}")
        failures += 1
    else:
        if orig_ret == obf_ret:
            print(f"  ok    100KB-noise in real Lua  ret={orig_ret}")
        else:
            print(f"  FAIL  100KB-noise: {orig_ret!r} != {obf_ret!r}")
            failures += 1

    print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILURES'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
