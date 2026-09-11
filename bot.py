import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import importlib
import os
import sys
import uuid
import logging
from pathlib import Path
import threading
from flask import Flask, jsonify


def _import_local_obfuscator():
    """Import local_obfuscator.py from next to bot.py, or from the first
    parent directory that contains it.

    The deployment supervisor launches `python3 -u bot.py` from an isolated
    per-instance subdirectory (e.g. /opt/render/project/src/vps_instances/vps-xxxx/)
    where only bot.py is copied. Python only puts the script's own directory
    on sys.path, so a plain `from local_obfuscator import obfuscate_lua`
    crashes with ModuleNotFoundError. Walking up the tree finds the module
    wherever the rest of the repo lives.
    """

    def _load():
        try:
            module = importlib.import_module("local_obfuscator")
        except ImportError:
            return None
        return module if hasattr(module, "obfuscate_lua") else None

    module = _load()
    if module is not None:
        return module.obfuscate_lua

    here = Path(__file__).resolve().parent
    for directory in (here, *here.parents):
        if not (directory / "local_obfuscator.py").is_file():
            continue
        # Drop any stale cached copy (e.g. an outdated local_obfuscator.py
        # that exists next to bot.py but lacks obfuscate_lua).
        sys.modules.pop("local_obfuscator", None)
        directory_str = str(directory)
        if directory_str not in sys.path:
            sys.path.insert(0, directory_str)
        module = _load()
        if module is not None:
            return module.obfuscate_lua

    raise ImportError(
        "local_obfuscator.py (with obfuscate_lua) was not found next to "
        "bot.py or in any parent directory — make sure it ships with the "
        "deployment."
    )


obfuscate_lua = _import_local_obfuscator()

# =====================================================================
# Luraph-style multi-layer obfuscation (no executor, no queue, no `lua` binary)
# ---------------------------------------------------------------------
# /obfuscate runs a pure-Python multi-layer obfuscator (local_obfuscator.py)
# right here on Render and returns the file immediately.
# 3-layer encoding: XOR+shift+permutation → reverse+XOR → control-flow
# flattened state machine, with opaque predicates, junk code, and anti-tamper.
# Nothing to install, no worker to keep online.
# =====================================================================

# ===== WEB SERVER (keep-alive for Render) =====
app = Flask(__name__)

ALLOWED_EXTENSIONS = {'.lua', '.txt'}
MAX_FILE_SIZE = 1_000_000

# Discord rejects attachments over 25MB (non-premium). The engine expands
# input ~170x (three encoding layers of 4-char byte escapes plus
# permutation tables), so cap the output and refuse oversized inputs
# early instead of burning CPU on a file that cannot be delivered.
MAX_OUTPUT_SIZE = 20_000_000
EST_OUTPUT_FACTOR = 175


@app.route('/')
def home():
    return jsonify({
        'status': 'online',
        'bot': 'Luraph Obfuscator Bot',
        'message': 'Bot is running 24/7',
        'mode': 'multi-layer-local',
        'layers': 3,
        'features': ['XOR+shift+permutation', 'control-flow flattening', 'opaque predicates', 'anti-tamper'],
    })


@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'uptime': 'running'})


def run_web_server():
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))


threading.Thread(target=run_web_server, daemon=True).start()
print("✅ Web server started")


# ===== DISCORD BOT =====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)


class ObfuscationError(Exception):
    pass


@bot.event
async def on_ready():
    logger.info(f"✅ Bot is ready! Logged in as {bot.user}")
    logger.info("✅ Luraph-style multi-layer obfuscation enabled (3-layer + anti-tamper)")

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


@bot.tree.command(name="obfuscate", description="Obfuscate a .lua or .txt file (instant)")
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

        logger.info(f"Obfuscating: {file.filename} ({len(code):,} chars)")

        # Cheap pre-check: refuse inputs whose estimated output would not
        # fit in a Discord attachment, before spending CPU on them.
        raw_len = len(code.encode('utf-8'))
        if raw_len * EST_OUTPUT_FACTOR > MAX_OUTPUT_SIZE:
            raise ObfuscationError(
                f"File too large to return: ~{raw_len // 1024}KB input would "
                f"obfuscate to ~{raw_len * EST_OUTPUT_FACTOR // 1024 // 1024}MB, "
                "over Discord's 25MB attachment limit. "
                f"Keep input under ~{MAX_OUTPUT_SIZE // EST_OUTPUT_FACTOR // 1024}KB."
            )

        # Run the (CPU-bound) obfuscator off the event loop so the bot stays
        # responsive, then reply immediately — no queue, no executor wait.
        try:
            obfuscated = await asyncio.to_thread(obfuscate_lua, code, file.filename)
        except ValueError as e:
            raise ObfuscationError(str(e))

        # Authoritative post-check on the actual output size.
        if len(obfuscated.encode('utf-8')) > MAX_OUTPUT_SIZE:
            raise ObfuscationError(
                "Obfuscated output exceeds Discord's attachment limit. "
                "Try a smaller input file."
            )

        output_name = f"obfuscated_{Path(file.filename).stem}.lua"
        job_id = uuid.uuid4().hex

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

    except ObfuscationError as e:
        await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
    except Exception as e:
        logger.exception("Obfuscation handler error")
        await interaction.followup.send(f"❌ Error: {str(e)[:200]}", ephemeral=True)


@bot.tree.command(name="obf_help", description="Show help for the obfuscator bot")
async def obf_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ Luraph Obfuscator Bot",
        description="Obfuscate Lua code with multi-layer Luraph-style protection — instantly, no setup needed.",
        color=0x00ff00
    )
    embed.add_field(
        name="📝 How to use",
        value="1. Attach a `.lua` or `.txt` file\n2. Type `/obfuscate`\n3. Get obfuscated file back instantly",
        inline=False
    )
    embed.add_field(
        name="🔒 Protection layers",
        value=(
            "• **Layer 1**: XOR + byte-shift + permutation encoding\n"
            "• **Layer 2**: Reverse + XOR decoder wrapping\n"
            "• **Layer 3**: Control-flow flattened state machine\n"
            "• Opaque predicates, junk code, anti-tamper checksums\n"
            "• Fresh random keys/names every run"
        ),
        inline=False
    )
    embed.add_field(
        name="⚠️ Limitations",
        value=(
            f"• Input: .lua / .txt only, up to 1MB\n"
            f"• Practical limit: ~{MAX_OUTPUT_SIZE // EST_OUTPUT_FACTOR // 1024}KB input "
            f"(obfuscated output must fit Discord's 25MB attachment cap)\n"
            "• Output needs `loadstring`/`load` enabled where it runs (standard in executors)"
        ),
        inline=False
    )
    embed.set_footer(text="All responses are ephemeral")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="obf_status", description="Check bot status")
async def obf_status(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 Bot Status", color=0x3498db)

    embed.add_field(
        name="⚡ Engine",
        value="✅ Luraph-style multi-layer obfuscator\n3-layer encoding + anti-tamper",
        inline=True
    )

    embed.add_field(
        name="🔒 Features",
        value="Control-flow flattening\nOpaque predicates\nJunk code injection",
        inline=True
    )

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
