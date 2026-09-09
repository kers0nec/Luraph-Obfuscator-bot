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
OBFUSCATOR_PATH = "obfuscator.lua"  # Your obfuscator file
ALLOWED_EXTENSIONS = {'.lua', '.txt'}
MAX_FILE_SIZE = 1_000_000  # 1MB


class ObfuscationError(Exception):
    pass


class Obfuscator:
    """Handles Lua obfuscation using obfuscator.lua"""
    
    @staticmethod
    async def obfuscate(code: str) -> str:
        """Run the obfuscator and return obfuscated code"""
        
        if not code or not code.strip():
            raise ObfuscationError("No code provided to obfuscate.")
        
        # Check if obfuscator.lua exists
        if not os.path.exists(OBFUSCATOR_PATH):
            raise ObfuscationError(f"Obfuscator file not found: {OBFUSCATOR_PATH}")
        
        # Create temp input file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.lua', delete=False, encoding='utf-8') as f:
            f.write(code)
            input_path = f.name
        
        output_path = input_path.replace('.lua', '_obf.lua')
        
        # Lua script that loads obfuscator.lua
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
                error_msg = result.stderr or result.stdout
                logger.error(f"Obfuscator error: {error_msg}")
                raise ObfuscationError(f"Obfuscation failed: {error_msg[:200]}")
            
            if "ERROR" in result.stdout:
                error_msg = result.stdout.replace("ERROR", "").strip()
                raise ObfuscationError(f"Obfuscation error: {error_msg[:200]}")
            
            if not os.path.exists(output_path):
                raise ObfuscationError("No output file was generated.")
            
            with open(output_path, 'r', encoding='utf-8') as f:
                obfuscated = f.read()
            
            if not obfuscated or obfuscated.strip().startswith('ERROR'):
                raise ObfuscationError("Obfuscation produced empty or invalid result.")
            
            return obfuscated
            
        except subprocess.TimeoutExpired:
            raise ObfuscationError("Obfuscation timed out (90s). Code may be too complex.")
        except ObfuscationError:
            raise
        except Exception as e:
            logger.error(f"Obfuscation error: {e}")
            raise ObfuscationError(f"Obfuscation failed: {str(e)[:200]}")
        finally:
            # Cleanup
            for path in [input_path, output_path]:
                try:
                    if os.path.exists(path):
                        os.unlink(path)
                except:
                    pass


@bot.event
async def on_ready():
    logger.info(f"✅ Bot is ready! Logged in as {bot.user} (ID: {bot.user.id})")
    
    # Check if obfuscator.lua exists
    if os.path.exists(OBFUSCATOR_PATH):
        logger.info(f"✅ Obfuscator file found: {OBFUSCATOR_PATH}")
    else:
        logger.warning(f"⚠️ Obfuscator file NOT found: {OBFUSCATOR_PATH}")
    
    # Sync slash commands
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


# === SLASH COMMANDS ===

@bot.tree.command(name="obfuscate", description="Obfuscate a .lua or .txt file")
@app_commands.describe(
    file="The .lua or .txt file to obfuscate"
)
async def obfuscate(interaction: discord.Interaction, file: discord.Attachment = None):
    """Obfuscate Lua code from a file attachment"""
    
    # Check for file attachment
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
        embed.add_field(
            name="Example",
            value="Send a message with a file attached, then type `/obfuscate`",
            inline=False
        )
        embed.set_footer(text="All responses are ephemeral (only visible to you)")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Validate file
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            await interaction.followup.send(
                f"❌ Unsupported file type. Please use `.lua` or `.txt` files.\nReceived: `{file.filename}`",
                ephemeral=True
            )
            return
        
        if file.size > MAX_FILE_SIZE:
            await interaction.followup.send(
                f"❌ File is too large! Max size: {MAX_FILE_SIZE // 1_000_000}MB\nYour file: {file.size // 1024}KB",
                ephemeral=True
            )
            return
        
        # Read the file
        try:
            content = await file.read()
            code = content.decode('utf-8')
        except UnicodeDecodeError:
            await interaction.followup.send(
                "❌ File must be UTF-8 encoded text.",
                ephemeral=True
            )
            return
        
        if not code.strip():
            await interaction.followup.send("❌ File is empty!", ephemeral=True)
            return
        
        logger.info(f"Processing file: {file.filename} ({len(code):,} chars)")
        
        # Obfuscate
        obfuscated = await Obfuscator.obfuscate(code)
        
        # Send result as file
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
        await interaction.followup.send(f"❌ An error occurred: {str(e)[:200]}", ephemeral=True)


@bot.tree.command(name="obf_help", description="Show help for the obfuscator bot")
async def obf_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ Lua Obfuscator Bot",
        description="Obfuscate Lua code from file attachments",
        color=0x00ff00
    )
    
    embed.add_field(
        name="📝 How to use",
        value=(
            "1. Attach a `.lua` or `.txt` file to your message\n"
            "2. Type `/obfuscate` in the same message\n"
            "3. The bot will reply with the obfuscated file"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📌 Example",
        value="Send a message with `script.lua` attached, then use the `/obfuscate` command",
        inline=False
    )
    
    embed.add_field(
        name="⚠️ Limitations",
        value=(
            "• Max file size: 1MB\n"
            "• Only `.lua` and `.txt` files\n"
            "• Timeout: 90 seconds"
        ),
        inline=False
    )
    
    embed.set_footer(text="All responses are ephemeral (only visible to you)")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="obf_status", description="Check bot status")
async def obf_status(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📊 Bot Status",
        color=0x3498db
    )
    
    # Check Lua
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(['lua', '-v'], capture_output=True, text=True, timeout=5)
        )
        lua_version = result.stdout.split('\n')[0][:50] if result.stdout else "Unknown"
        embed.add_field(
            name="🔧 Lua",
            value=f"✅ {lua_version}" if result.returncode == 0 else "❌ Not found",
            inline=True
        )
    except:
        embed.add_field(name="🔧 Lua", value="❌ Not found", inline=True)
    
    # Check obfuscator.lua
    if os.path.exists(OBFUSCATOR_PATH):
        with open(OBFUSCATOR_PATH, 'r') as f:
            size = len(f.read())
        embed.add_field(
            name="📁 obfuscator.lua",
            value=f"✅ Found ({size:,} chars)",
            inline=True
        )
    else:
        embed.add_field(
            name="📁 obfuscator.lua",
            value="❌ Not found",
            inline=True
        )
    
    embed.add_field(
        name="🤖 Bot",
        value=f"✅ Online\nUser: {bot.user}",
        inline=True
    )
    
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
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
