import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import uuid
import os
import logging
from pathlib import Path
import threading
from flask import Flask, jsonify

from local_obfuscator import obfuscate_lua

# =====================================================================
# Instant local obfuscation (no executor, no queue, no `lua` binary)
# ---------------------------------------------------------------------
# /obfuscate runs a pure-Python obfuscator (local_obfuscator.py) right here
# on Render and returns the file immediately. Nothing to install, no worker
# to keep online, no "Queued ... waiting for the executor" delay.
# =====================================================================

# ===== WEB SERVER (keep-alive for Render) =====
app = Flask(__name__)

ALLOWED_EXTENSIONS = {'.lua', '.txt'}
MAX_FILE_SIZE = 1_000_000


@app.route('/')
def home():
    return jsonify({
        'status': 'online',
        'bot': 'Lua Obfuscator Bot',
        'message': 'Bot is running 24/7',
        'mode': 'instant-local',
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
    logger.info("✅ Instant local obfuscation enabled (no executor needed)")

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

        # Run the (CPU-bound) obfuscator off the event loop so the bot stays
        # responsive, then reply immediately — no queue, no executor wait.
        try:
            obfuscated = await asyncio.to_thread(obfuscate_lua, code, file.filename)
        except ValueError as e:
            raise ObfuscationError(str(e))

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
        title="🛡️ Lua Obfuscator Bot",
        description="Obfuscate Lua code from file attachments — instantly, no setup needed.",
        color=0x00ff00
    )
    embed.add_field(
        name="📝 How to use",
        value="1. Attach a `.lua` or `.txt` file\n2. Type `/obfuscate`\n3. Get obfuscated file back instantly",
        inline=False
    )
    embed.add_field(
        name="⚠️ Limitations",
        value="• Max file size: 1MB\n• Only `.lua` and `.txt`\n• Output needs `loadstring`/`load` enabled where it runs (standard in executors)",
        inline=False
    )
    embed.set_footer(text="All responses are ephemeral")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="obf_status", description="Check bot status")
async def obf_status(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 Bot Status", color=0x3498db)

    embed.add_field(
        name="⚡ Engine",
        value="✅ Instant local obfuscation",
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
