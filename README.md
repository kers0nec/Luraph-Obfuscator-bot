# Luraph Obfuscator Bot

A Discord bot that returns strongly obfuscated Lua files — **instantly**.
No executor, no queue, no `lua` binary to install.

## How it works

`/obfuscate` runs a pure-Python multi-layer obfuscator (`local_obfuscator.py`)
right on Render and replies with the file immediately:

```
Discord ──> Render bot (bot.py + local_obfuscator.py) ──> obfuscated file
```

The engine uses **Luraph-style multi-layer obfuscation**:

| Layer | Encoding | Purpose |
|-------|----------|---------|
| Layer 1 (Inner) | XOR + byte-shift + byte permutation | Encodes the original source |
| Layer 2 (Middle) | Reverse + XOR with different key | Encodes the layer-1 decoder |
| Layer 3 (Outer) | Byte-shift with control-flow flattening | State-machine loader for layer 2 |

Additional hardening features:
- **Opaque predicates** — always-true/false decoy branches confuse analysis
- **Junk code injection** — dead code blocks that look real but never execute
- **Anti-tamper checksums** — payload integrity verification before execution
- **Randomized naming** — every variable, key, and state is fresh per run
- **Permutation shuffling** — byte positions scrambled with random seed
- **Multi-cipher chain** — each layer uses a different encoding scheme

Each run uses a fresh random seed, so every output looks completely different.
Return values and `...` varargs are forwarded, so both Scripts and ModuleScripts
keep working.

The output only needs `string` / `table` / `loadstring` where it runs —
available in stock Lua 5.1+ and Roblox Luau executors.

> Note: the bundled `obfuscator.lua` is Luraph-protected **sample output**, not
> an obfuscation engine (it needs Roblox-only globals and can't run on Render).
> It is kept for reference only — the bot does not use it.

## Setup (Render)

* Set one **environment variable** on the service:
  * `DISCORD_TOKEN` — your bot token.
* Deploy from the `render.yaml` blueprint. That's it — no `BRIDGE_KEY`, no
  worker to keep online.

If you previously used the executor bridge: you can delete the old `BRIDGE_KEY`
env var from the Render dashboard and stop running `executor_worker.lua`
(it has been removed from this repo).

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Discord bot (instant obfuscation, no queue). |
| `local_obfuscator.py` | Luraph-style multi-layer Lua obfuscation engine (3-layer, anti-tamper, control-flow flattening). |
| `render.yaml` | Render blueprint for the bot service. |
| `obfuscator.lua` | Luraph-protected sample output — reference only, not used. |

## Commands

* `/obfuscate <file>` — obfuscate a file instantly (`.lua` / `.txt`, ≤ 1 MB).
* `/obf_help` — usage help.
* `/obf_status` — bot status.
