# Luraph Obfuscator Bot

A Discord bot that returns obfuscated Lua files, using a **delegated**
architecture because of a hard technical reality (explained below).

## The problem you were hitting

```
❌ Obfuscation failed: [Errno 2] No such file or directory: 'lua'
```

The old code tried to run obfuscation itself by shelling out to a `lua`
interpreter:

```python
subprocess.run(['lua', ...])
```

Two things made that fail — and no amount of "installing lua" fixes either of them:

1. **`lua` isn't installed where the bot runs.** On Render, installing packages in
   `buildCommand` does *not* carry over to the running container, so `lua` was missing.
2. **The bundled `obfuscator.lua` can't run under normal Lua at all.** It starts with
   `-- This file was protected using Luraph Obfuscator v15.0` and is **Luraph-protected
   sample output**, not an obfuscation engine. It's written for Roblox's LuaU runtime
   and needs Roblox globals (`game`, `buffer`, `Vector3`, `typeof`, …) plus LuaU-only
   syntax like `continue`. We verified this by building stock Lua 5.1 / 5.3 / 5.4
   interpreters and trying to parse it — all fail with a syntax error near the first
   `continue`.

## How it works now (delegation bridge)

The only place real Luraph-style obfuscation can run is **inside a Roblox executor**.
So this bot no longer tries to do the work on Render. Instead:

```
 Discord ──> Render bot (bot.py) ──> job queue ──> Roblox executor (executor_worker.lua)
    ^                                                                |
    └────────────────────────── obfuscated file ◀────────────────────┘
```

1. User uploads a `.lua` / `.txt` and runs `/obfuscate`.
2. `bot.py` (on Render) queues an obfuscation **job** and exposes it over HTTP.
3. `executor_worker.lua` (running in your Roblox executor) polls Render, claims the
   job, obfuscates the source, and posts the result back.
4. `bot.py` returns the obfuscated file to the Discord user.

The two sides authenticate with a shared **`BRIDGE_KEY`** so outsiders can't use your
executor or inject fake results.

## Setup

### 1. Render (the bot)

* Set these **environment variables** on the service:
  * `DISCORD_TOKEN` — your bot token.
  * `BRIDGE_KEY` — a long random secret (e.g. `openssl rand -hex 32`).
* Deploy from the `render.yaml` blueprint (it no longer tries to install `lua`).

### 2. Roblox executor (the worker)

* Open `executor_worker.lua` and set:
  * `RENDER_URL` to your Render service URL.
  * `BRIDGE_KEY` to the **same** value used in Render.
* **Wire up `obfuscate_source()`** to your real obfuscation engine (see below).
* Run `executor_worker.lua` inside your executor while a Roblox game is open.

### 3. The obfuscation engine (read this!)

`obfuscator.lua` in this repo is **Luraph-protected sample output, not an engine.**
You cannot use it to obfuscate new code. To make the bot produce real obfuscated
scripts, point `obfuscate_source()` in `executor_worker.lua` at the actual
Luraph / obfuscation tool your executor uses. Everything else (queue, bridge,
Discord delivery) is ready to go once that single function is connected.

If you expected this repo to *contain* the engine, note that Luraph is a commercial,
server-side obfuscator — its engine isn't distributed as a plain `.lua` file you can
host on Render. `obfuscator.lua` here is an example of Luraph's *output*.

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Discord bot + HTTP obfuscation bridge (job queue, result relay). |
| `executor_worker.lua` | Polls Render for jobs and runs the real obfuscator in a Roblox executor. |
| `render.yaml` | Render blueprint for the bot service. |
| `obfuscator.lua` | Luraph-protected sample output (not an engine) — kept for reference. |

## HTTP API (bridge)

All endpoints require header `X-Bridge-Key: <BRIDGE_KEY>`.

| Method & path | Purpose |
|---------------|---------|
| `GET /api/job/next` | Executor claims the next queued job. |
| `POST /api/job/<id>/result` | Executor posts the obfuscated result. |
| `GET /api/job/<id>` | Job status check. |
| `GET /api/assets/obfuscator.lua` | Optionally serves the bundled file to the executor. |

## Commands

* `/obfuscate <file>` — queue a file for obfuscation (`.lua` / `.txt`, ≤ 1 MB).
* `/obf_help` — usage help.
* `/obf_status` — bot, bridge, and queue status.
