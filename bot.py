import discord
from discord.ext import commands
from discord import app_commands
import uuid
import time
import os
import logging
from pathlib import Path
import asyncio
import threading
from flask import Flask, request, jsonify

# =====================================================================
# Delegate architecture (Luraph obfuscator)
# ---------------------------------------------------------------------
# obfuscator.lua is a Roblox / Luau-only artifact.  It needs globals that
# only exist inside a Roblox executor (game, buffer, Vector3, typeof,
# setfenv, the `continue` keyword, ...).  A plain Linux host (Render) can
# never run it — installing stock `lua` does not help.
#
# So this bot does NOT try to run obfuscator.lua itself.  Instead:
#
#   1. A user uploads a .lua/.txt file through Discord.
#   2. This bot (hosted on Render) queues an obfuscation JOB in memory and
#      replies once the job completes.
#   3. A Roblox executor worker (executor_worker.lua, running on your PC
#      inside an executor that CAN run obfuscator.lua) polls the Render
#      HTTP endpoints below for jobs, obfuscates the code, and posts the
#      result back here.
#   4. The bot hands the result back to the Discord user as a file.
#
# The two sides share a secret (env: BRIDGE_KEY) so random people on the
# internet can't use your executor or inject fake results.
# =====================================================================

# ===== WEB SERVER (HTTP bridge + keep-alive) =====
app = Flask(__name__)

# Job store shared between the Flask (bridge) thread and the Discord async
# handlers.  Format:
#   JOBS[job_id] = {
#       'status': 'queued' | 'in_progress' | 'done' | 'error',
#       'code': str,            # original source to obfuscate
#       'filename': str,        # original user filename
#       'result': str | None,   # obfuscated output when done
#       'error': str | None,    # message when failed
#       'claimed_at': float,
#   }
JOBS = {}

BRIDGE_KEY = os.getenv('BRIDGE_KEY', '')  # shared secret with the executor
OBFUSCATOR_PATH = Path('obfuscator.lua')
ALLOWED_EXTENSIONS = {'.lua', '.txt'}
MAX_FILE_SIZE = 1_000_000
JOB_TIMEOUT_SECONDS = 180  # how long the Discord command waits for the executor
POP_TIMEOUT_SECONDS = 300  # how long a claimed job may be worked on


def _authorized() -> bool:
    """Require the X-Bridge-Key header to match BRIDGE_KEY."""
    if not BRIDGE_KEY:
        return False
    return request.headers.get('X-Bridge-Key') == BRIDGE_KEY


@app.route('/')
def home():
    return jsonify({
        'status': 'online',
        'bot': 'Lua Obfuscator Bot',
        'message': 'Bot is running 24/7',
        'pending_jobs': sum(1 for j in JOBS.values() if j['status'] == 'queued'),
    })


@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'uptime': 'running'})


@app.route('/api/job/next', methods=['GET'])
def job_next():
    """Executor polls this to claim the next queued job."""
    if not _authorized():
        return jsonify({'error': 'unauthorized'}), 401
    now = time.time()
    # Claim the oldest queued job.
    for job_id, job in sorted(JOBS.items(), key=lambda kv: kv[1].get('created_at', 0)):
        if job['status'] == 'queued':
            job['status'] = 'in_progress'
            job['claimed_at'] = now
            return jsonify({
                'job_id': job_id,
                'code': job['code'],
                'filename': job['filename'],
            }), 200
        # Free up jobs claimed but abandoned by a dead executor.
        if job['status'] == 'in_progress' and now - job.get('claimed_at', 0) > POP_TIMEOUT_SECONDS:
            job['status'] = 'queued'
    return jsonify({}), 204


@app.route('/api/job/<job_id>/result', methods=['POST'])
def job_result(job_id):
    """Executor posts the obfuscation result here."""
    if not _authorized():
        return jsonify({'error': 'unauthorized'}), 401
    job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'unknown job'}), 404
    data = request.get_json(silent=True) or {}
    if data.get('success'):
        job['status'] = 'done'
        job['result'] = data.get('output', '')
    else:
        job['status'] = 'error'
        job['error'] = str(data.get('error', 'Unknown executor error'))[:500]
    return jsonify({'ok': True}), 200


@app.route('/api/job/<job_id>', methods=['GET'])
def job_status(job_id):
    """Optional status check (used by the executor / debugging)."""
    if not _authorized():
        return jsonify({'error': 'unauthorized'}), 401
    job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'unknown job'}), 404
    return jsonify({'job_id': job_id, 'status': job['status']}), 200


@app.route('/api/assets/obfuscator.lua', methods=['GET'])
def obfuscator_asset():
    """Serves obfuscator.lua so the executor doesn't need a local copy.

    The executor fetches this, then loadstring()'s it inside Roblox where
    the required Roblox globals exist.
    """
    if not _authorized():
        return jsonify({'error': 'unauthorized'}), 401
    if not OBFUSCATOR_PATH.exists():
        return jsonify({'error': 'obfuscator.lua not present on server'}), 500
    return OBFUSCATOR_PATH.read_text(encoding='utf-8'), 200, {
        'Content-Type': 'text/plain; charset=utf-8',
        'Cache-Control': 'no-store',
    }


def run_web_server():
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))


threading.Thread(target=run_web_server, daemon=True).start()
print("✅ Web server / obfuscation bridge started")


# ===== DISCORD BOT =====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)


class ObfuscationError(Exception):
    pass


def _queue_job(code: str, filename: str) -> str:
    """Add a job to the queue and return its id."""
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        'status': 'queued',
        'code': code,
        'filename': filename,
        'result': None,
        'error': None,
        'created_at': time.time(),
        'claimed_at': None,
    }
    return job_id


async def _wait_for_job(job_id: str, timeout: int = JOB_TIMEOUT_SECONDS) -> str:
    """Wait (async, non-blocking) until the executor finishes a job.

    Raises ObfuscationError on executor failure / timeout.
    """
    job = JOBS[job_id]
    elapsed = 0.0
    while job['status'] in ('queued', 'in_progress'):
        if elapsed >= timeout:
            # No executor finished this in time — drop it so we don't leak
            # memory while the executor is offline.
            JOBS.pop(job_id, None)
            raise ObfuscationError(
                "Timed out waiting for the obfuscation executor. "
                "Make sure `executor_worker.lua` is running in a Roblox executor."
            )
        await asyncio.sleep(1.0)
        elapsed += 1.0

    if job['status'] == 'error':
        raise ObfuscationError(job['error'] or "Executor reported a failure.")
    if job['status'] == 'done':
        result = job.get('result') or ''
        if not result.strip():
            raise ObfuscationError("Executor returned an empty result.")
        return result
    raise ObfuscationError("Unexpected job state.")


@bot.event
async def on_ready():
    logger.info(f"✅ Bot is ready! Logged in as {bot.user}")

    if OBFUSCATOR_PATH.exists():
        logger.info(f"✅ obfuscator.lua found on server ({OBFUSCATOR_PATH.stat().st_size:,} bytes)")
    else:
        logger.warning(f"⚠️ obfuscator.lua NOT found at {OBFUSCATOR_PATH}")

    if not BRIDGE_KEY:
        logger.warning("⚠️ BRIDGE_KEY env var not set — the executor will not be able to connect!")
    else:
        logger.info("✅ BRIDGE_KEY is set (executor bridge enabled)")

    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="/obfuscate"
        )
    )


@bot.tree.command(name="obfuscate", description="Obfuscate a .lua or .txt file (runs via the Roblox executor)")
@app_commands.describe(file="The .lua or .txt file to obfuscate")
async def obfuscate(interaction: discord.Interaction, file: discord.Attachment = None):
    if not file:
        if interaction.message and interaction.message.attachments:
            file = interaction.message.attachments[0]

    if not file:
        embed = discord.Embed(
            title="📝 How to use /obfuscate",
            description="Attach a file to obfuscate:",
            color=0x3498db
        )
        embed.add_field(
            name="Usage",
            value="1. Attach a `.lua` or `.txt` file to your message\n2. Use `/obfuscate` in the same message",
            inline=False
        )
        embed.set_footer(text="All responses are ephemeral (only visible to you)")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            await interaction.followup.send(
                f"❌ Unsupported file type. Use `.lua` or `.txt`.\nReceived: `{file.filename}`",
                ephemeral=True
            )
            return

        if file.size > MAX_FILE_SIZE:
            await interaction.followup.send(
                f"❌ File too large! Max: 1MB\nYour file: {file.size // 1024}KB",
                ephemeral=True
            )
            return

        try:
            content = await file.read()
            code = content.decode('utf-8')
        except UnicodeDecodeError:
            await interaction.followup.send("❌ File must be UTF-8 encoded.", ephemeral=True)
            return

        if not code.strip():
            await interaction.followup.send("❌ File is empty!", ephemeral=True)
            return

        if not BRIDGE_KEY:
            await interaction.followup.send(
                "❌ This bot isn't configured for obfuscation yet "
                "(missing `BRIDGE_KEY` environment variable). Contact the bot owner.",
                ephemeral=True
            )
            return

        logger.info(f"Queueing: {file.filename} ({len(code):,} chars)")

        job_id = _queue_job(code, file.filename)
        output_name = f"obfuscated_{Path(file.filename).stem}.lua"

        await interaction.followup.send(
            f"⏳ Queued `{file.filename}` for obfuscation… "
            f"(waiting for the executor to pick it up)",
            ephemeral=True
        )

        obfuscated = await _wait_for_job(job_id)

        # Deliver the result back as a file.
        temp = Path(f"/tmp/obf_result_{job_id}.lua")
        temp.write_text(obfuscated, encoding='utf-8')

        try:
            await interaction.followup.send(
                f"✅ Obfuscated `{file.filename}`\n"
                f"Original: {len(code):,} chars → Obfuscated: {len(obfuscated):,} chars",
                file=discord.File(str(temp), filename=output_name),
                ephemeral=True
            )
        finally:
            try:
                temp.unlink()
            except OSError:
                pass

        # Free memory once the user has the result.
        JOBS.pop(job_id, None)

    except ObfuscationError as e:
        await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
    except Exception as e:
        logger.exception("Obfuscation handler error")
        await interaction.followup.send(f"❌ Error: {str(e)[:200]}", ephemeral=True)


@bot.tree.command(name="obf_help", description="Show help for the obfuscator bot")
async def obf_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ Lua Obfuscator Bot",
        description="Obfuscate Lua code from file attachments",
        color=0x00ff00
    )
    embed.add_field(
        name="📝 How to use",
        value="1. Attach a `.lua` or `.txt` file\n2. Type `/obfuscate`\n3. Get obfuscated file back",
        inline=False
    )
    embed.add_field(
        name="⚠️ Limitations",
        value="• Max file size: 1MB\n• Only `.lua` and `.txt`\n• Obfuscation runs through the Roblox executor (see README)",
        inline=False
    )
    embed.set_footer(text="All responses are ephemeral")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="obf_status", description="Check bot status")
async def obf_status(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 Bot Status", color=0x3498db)

    obf_size = OBFUSCATOR_PATH.stat().st_size if OBFUSCATOR_PATH.exists() else None
    embed.add_field(
        name="📁 obfuscator.lua",
        value=f"✅ Found ({obf_size:,} bytes)" if obf_size else "❌ Not found on server",
        inline=True
    )

    bridge_status = "✅ Configured" if BRIDGE_KEY else "❌ Missing BRIDGE_KEY"
    embed.add_field(name="🔗 Bridge", value=bridge_status, inline=True)

    pending = sum(1 for j in JOBS.values() if j['status'] == 'queued')
    active = sum(1 for j in JOBS.values() if j['status'] == 'in_progress')
    embed.add_field(name="📥 Queue", value=f"{pending} waiting · {active} working", inline=True)

    embed.add_field(name="🤖 Bot", value=f"✅ Online\nUser: {bot.user}", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        logger.error("❌ DISCORD_TOKEN environment variable not set!")
        exit(1)

    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("❌ Invalid Discord token!")
    except Exception as e:
        logger.error(f"❌ Bot startup error: {e}")
