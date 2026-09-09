# Luraph Obfuscator Bot

A Discord bot that returns obfuscated Lua files — **instantly**. No executor,
no queue, no `lua` binary to install.

## How it works

`/obfuscate` runs a pure-Python obfuscator (`local_obfuscator.py`) right on
Render and replies with the file immediately:

```
Discord ──> Render bot (bot.py + local_obfuscator.py) ──> obfuscated file
```

The engine byte-shift-encodes the entire source, embeds it as decimal-escaped
string chunks, and emits a small loader that decodes it at runtime and executes
it via `loadstring`/`load`. Each run uses a fresh random key and fresh random
local names, so every output looks different. Return values and `...` varargs
are forwarded, so both Scripts and ModuleScripts keep working.

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
| `local_obfuscator.py` | Pure-Python Lua obfuscation engine. |
| `render.yaml` | Render blueprint for the bot service. |
| `obfuscator.lua` | Luraph-protected sample output — reference only, not used. |

## Commands

* `/obfuscate <file>` — obfuscate a file instantly (`.lua` / `.txt`, ≤ 1 MB).
* `/obf_help` — usage help.
* `/obf_status` — bot status.
