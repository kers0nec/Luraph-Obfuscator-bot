import discord
from discord.ext import commands
from discord import app_commands
import subprocess
import tempfile
import os
import logging
from pathlib import Path
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.attachments = True

bot = commands.Bot(command_prefix='/', intents=intents)

# Constants
OBFUSCATOR_PATH = "obfuscator.lua"
ALLOWED_EXTENSIONS = {'.lua', '.txt'}
MAX_FILE_SIZE = 1_000_000


class ObfuscationError(Exception):
    pass


class Obfuscator:
    @staticmethod
    async def obfuscate(code: str) -> str:
        if not code or not code.strip():
            raise ObfuscationError("No code provided.")
        
        if not os.path.exists(OBFUSCATOR_PATH):
            raise ObfuscationError(f"Obfuscator file not found: {OBFUSCATOR_PATH}")
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.lua', delete=False, encoding='utf-8') as f:
            f.write(code)
            input_path = f.name
        
        output_path = input_path.replace('.lua', '_obf.lua')
        
        lua_script = f"""
        local obf = dofile('{OBFUSCATOR_PATH}')
        
        local function obfuscate_code(code)
            return obf:Q()(code)
        end
        
        local code = io.open('{input_path}', 'r'):read('*a')
        local output = io.open('{output_path}', 'w')
        
        local success, result = pcall(obfuscate_code, code)
        if not success then
            output:write('ERROR: ' .. tostring(result))
        else
            output:write(result or '')
        end
        output:close()
        
        if not success then
            print('ERROR')
            print(tostring(result))
        else
            print('SUCCESS')
        end
        """
        
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ['lua', '-e', lua_script],
                    capture_output=True,
                    text=True,
                    timeout=90
                )
            )
            
            if result.returncode != 0:
                raise ObfuscationError(f"Obfuscation failed: {result.stderr[:200]}")
            
            if "ERROR" in result.stdout:
                raise ObfuscationError(result.stdout.replace("ERROR", "").strip()[:200])
            
            if not os.path.exists(output_path):
                raise ObfuscationError("No output generated.")
            
            with open(output_path, 'r', encoding='utf-8') as f:
                return f.read()
            
        except subprocess.TimeoutExpired:
            raise ObfuscationError("Timeout (90s)")
        except Exception as e:
            logger.error(f"Obfuscation error: {e}")
            raise ObfuscationError(f"Obfuscation failed: {str(e)[:200]}")
        finally:
            for path in [input_path, output_path]:
                try:
                    if os.path.exists(path):
                        os.unlink(path)
                except:
                    pass


@bot.event
async def on_ready():
    logger.info(f"✅ Bot is ready! Logged in as {bot.user}")
    
    if os.path.exists(OBFUSCATOR_PATH):
        logger.info(f"✅ Obfuscator file found: {OBFUSCATOR_PATH}")
    else:
        logger.warning(f"⚠️ Obfuscator file NOT found: {OBFUSCATOR_PATH}")
    
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


@bot.tree.command(name="obfuscate", description="Obfuscate a .lua or .txt file")
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
        
        logger.info(f"Processing: {file.filename} ({len(code):,} chars)")
        
        obfuscated = await Obfuscator.obfuscate(code)
        
        output_name = f"obfuscated_{Path(file.filename).stem}.lua"
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.lua', delete=False, encoding='utf-8') as f:
            f.write(obfuscated)
            file_path = f.name
        
        await interaction.followup.send(
            f"✅ Obfuscated `{file.filename}`\n"
            f"Original: {len(code):,} chars → Obfuscated: {len(obfuscated):,} chars",
            file=discord.File(file_path, filename=output_name),
            ephemeral=True
        )
        
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
        except:
            pass
        
    except ObfuscationError as e:
        await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
    except Exception as e:
        logger.error(f"Obfuscation error: {e}")
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
        value="• Max file size: 1MB\n• Only `.lua` and `.txt`\n• Timeout: 90 seconds",
        inline=False
    )
    embed.set_footer(text="All responses are ephemeral")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="obf_status", description="Check bot status")
async def obf_status(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 Bot Status", color=0x3498db)
    
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(['lua', '-v'], capture_output=True, text=True, timeout=5)
        )
        lua_version = result.stdout.split('\n')[0][:50] if result.stdout else "Unknown"
        embed.add_field(name="🔧 Lua", value=f"✅ {lua_version}" if result.returncode == 0 else "❌ Not found", inline=True)
    except:
        embed.add_field(name="🔧 Lua", value="❌ Not found", inline=True)
    
    if os.path.exists(OBFUSCATOR_PATH):
        with open(OBFUSCATOR_PATH, 'r') as f:
            size = len(f.read())
        embed.add_field(name="📁 obfuscator.lua", value=f"✅ Found ({size:,} chars)", inline=True)
    else:
        embed.add_field(name="📁 obfuscator.lua", value="❌ Not found", inline=True)
    
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
