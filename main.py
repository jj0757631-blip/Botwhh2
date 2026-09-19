# made by gih (https://github.com/glockinhand)
# there might be lots of code that isnt even used anymore i was too lazy to remove it
import os
import re
import io
import json
import time
import base64
import asyncio
import random
import logging
import requests
import aiohttp
from io import BytesIO
from datetime import datetime, timedelta
from colorama import Fore, Style, init
import discord
from discord import User, Embed, Interaction, Permissions, AllowedMentions, ButtonStyle, app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput, View, Button
from PIL import Image, ImageDraw, ImageFont, ImageOps

import requests

init(autoreset=True)

LOG_WEBHOOK_URL = ""  # filled from config.json on load
PREMIUM_FILE = "premium.json"
PRESETS_FILE = "presets.json"
BLACKLIST_FILE = "blacklist.json"


IPLOGGER_API_KEY = "api_mluj4uO5oB5Uxnf2is0qDyYsjDecZ3Nj" # only if u want to use iplogger command


async def log_command_use(user=None, command_name: str = "unknown", channel=None, message: str = None, **kwargs):
    """Send command-use log to the owner-only webhook. Handles all call styles in this file."""
    global LOG_WEBHOOK_URL
    if not LOG_WEBHOOK_URL:
        return
    try:
        uid = getattr(user, "id", None) or "?"
        uname = getattr(user, "name", None) or getattr(user, "display_name", None) or str(user)
        mention = getattr(user, "mention", None) or f"`{uname}`"
        ch_name = getattr(channel, "name", None) or (str(channel) if channel else "DM / unknown")
        guild = getattr(channel, "guild", None)
        g_name = guild.name if guild else "DM"

        embed = discord.Embed(
            title="Command Log",
            colour=0xa874d1,
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="User", value=f"{mention}\n`{uname}` (`{uid}`)", inline=True)
        embed.add_field(name="Command", value=f"`{command_name}`", inline=True)
        embed.add_field(name="Server", value=g_name, inline=True)
        embed.add_field(name="Channel", value=f"#{ch_name}", inline=True)
        if message:
            preview = str(message)[:900]
            embed.add_field(name="Content / Args", value=f"```{preview}```", inline=False)
        if user and getattr(user, "display_avatar", None):
            embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="Havoc · owner log")

        async with aiohttp.ClientSession() as session:
            webhook = discord.Webhook.from_url(LOG_WEBHOOK_URL, session=session)
            await webhook.send(embed=embed, username="Havoc Logs", wait=False)
    except Exception as e:
        print(f"[log_command_use] failed: {e}")


class RateLimitFilter(logging.Filter):
    def filter(self, record):
        if "is rate limited" in record.getMessage():
            if not hasattr(record, "already_logged"):
                record.already_logged = True
            return False 
        return True  

logger = logging.getLogger("discord.webhook.async_")
logger.addFilter(RateLimitFilter())

with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

LOG_WEBHOOK_URL = config.get("LOG_WEBHOOK_URL", "") or LOG_WEBHOOK_URL
# Users must be in this server + have the verified role to use the bot
REQUIRED_GUILD_ID = int(config.get("REQUIRED_GUILD_ID", 0) or 0)
REQUIRED_INVITE = config.get("REQUIRED_INVITE", "https://discord.gg/U3RussXskz")
REQUIRED_ROLE_NAME = config.get("REQUIRED_ROLE_NAME", "verified")  # case-insensitive name
REQUIRED_ROLE_ID = int(config.get("REQUIRED_ROLE_ID", 0) or 0)     # preferred if set

# Bot instance (was missing — caused NameError: bot is not defined)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def get_required_member(user_id: int):
    """Fetch member from the required guild, or None. Tries cache then API."""
    if not REQUIRED_GUILD_ID:
        return None
    guild = bot.get_guild(REQUIRED_GUILD_ID)
    if guild is None:
        print(f"[gate] bot is NOT in required guild {REQUIRED_GUILD_ID}")
        return None
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        member = await guild.fetch_member(user_id)
        return member
    except discord.NotFound:
        return None
    except discord.HTTPException as e:
        print(f"[gate] fetch_member failed for {user_id}: {e}")
        return None
    except Exception as e:
        print(f"[gate] unexpected fetch error for {user_id}: {e}")
        return None


def member_has_verified_role(member) -> bool:
    """True if member has the required verified role."""
    if member is None:
        return False
    # Owners / admins always pass role check so you don't lock yourself out
    if member.guild_permissions.administrator:
        return True
    if REQUIRED_ROLE_ID:
        return any(r.id == REQUIRED_ROLE_ID for r in member.roles)
    if REQUIRED_ROLE_NAME:
        name = REQUIRED_ROLE_NAME.lower().strip()
        return any(r.name.lower().strip() == name for r in member.roles)
    return True


async def user_allowed(user_id: int):
    """Returns (allowed: bool, reason: str). MUST be in REQUIRED_GUILD_ID to use any command."""
    # blacklist always blocks (owners can unblacklist themselves via owner cmds if needed)
    try:
        wl = config.get("whitelist", []) if isinstance(config, dict) else []
        is_owner = int(user_id) in [int(x) for x in wl]
    except Exception:
        is_owner = False

    try:
        if (not is_owner) and is_blacklisted(user_id):
            return False, (
                "**You are blacklisted from using this bot.**\n\n"
                "Contact the owner if you think this is a mistake."
            )
    except Exception:
        pass

    # Server membership is mandatory for EVERYONE (including whitelist owners).
    # Bot must be in REQUIRED_GUILD_ID and user must be a member there.
    if not REQUIRED_GUILD_ID:
        # misconfigured — fail closed so commands aren't free-for-all
        return False, (
            "**Bot gate misconfigured (no REQUIRED_GUILD_ID).**\n"
            "Owner must set REQUIRED_GUILD_ID in config.json."
        )

    member = await get_required_member(user_id)
    if member is None:
        return False, (
            "**You must be in the Havoc server to use this bot.**\n\n"
            f"Join here → {REQUIRED_INVITE}\n\n"
            "After joining, run the command again."
        )

    # optional role gate (only if configured)
    if (REQUIRED_ROLE_ID or (REQUIRED_ROLE_NAME and str(REQUIRED_ROLE_NAME).strip())):
        if not member_has_verified_role(member):
            role_hint = REQUIRED_ROLE_NAME or str(REQUIRED_ROLE_ID)
            return False, (
                f"**You need the `{role_hint}` role in the server.**\n\n"
                f"Get it, then try again.\n{REQUIRED_INVITE}"
            )
    return True, ""


async def _deny_interaction(interaction: discord.Interaction, msg: str) -> bool:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        print(f"[gate] failed to send deny: {e}")
    return False


@bot.tree.interaction_check
async def global_interaction_check(interaction: discord.Interaction) -> bool:
    """Global gate for slash commands."""
    if await hard_block_interaction(interaction):
        return False

    uid = interaction.user.id
    allowed, msg = await user_allowed(uid)
    if allowed:
        return True
    first = (msg or "denied").splitlines()[0]
    print(f"[gate] blocked {interaction.user} ({uid}) — {first}")
    return await _deny_interaction(interaction, msg or "You are not allowed to use this bot.")



@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    # still log failed attempts
    try:
        await log_command_use(
            user=interaction.user,
            command_name=f"ERROR /{interaction.command.name if interaction.command else '?'}",
            channel=interaction.channel,
            message=str(error)[:500],
        )
    except Exception:
        pass
    if not interaction.response.is_done():
        await interaction.response.send_message(f"Error: {error}", ephemeral=True)
    
class CooldownManager:
    def __init__(self, cooldown_seconds: int):
        self.cooldown_seconds = cooldown_seconds
        self.user_timestamps = {}

    def can_use(self, user_id: int) -> (bool, int):
        now = time.time()
        last_time = self.user_timestamps.get(user_id, 0)
        elapsed = now - last_time
        if elapsed >= self.cooldown_seconds:
            self.user_timestamps[user_id] = now
            self.cleanup()
            return True, 0
        else:
            return False, int(self.cooldown_seconds - elapsed)

    def cleanup(self):
        now = time.time()
        to_delete = [user for user, ts in self.user_timestamps.items() if now - ts > self.cooldown_seconds]
        for user in to_delete:
            del self.user_timestamps[user]

cooldown_manager = CooldownManager(100)


def load_premium_users():
    if not os.path.exists(PREMIUM_FILE):
        return []
    with open(PREMIUM_FILE, "r") as f:
        return json.load(f)

def save_premium_users(user_ids):
    with open(PREMIUM_FILE, "w") as f:
        json.dump(user_ids, f, indent=2)

def add_premium_user(user_id: int):
    premium_users = load_premium_users()
    if user_id not in premium_users:
        premium_users.append(user_id)
        save_premium_users(premium_users)

def is_premium_user(user_id: int):
    premium_users = load_premium_users()
    return user_id in premium_users

def remove_premium_user(user_id: int) -> bool:
    premium_users = load_premium_users()
    if user_id in premium_users:
        premium_users.remove(user_id)
        save_premium_users(premium_users)
        return True

_blacklist_cache = None  # list[int] | None

def _blacklist_path():
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, BLACKLIST_FILE)

def load_blacklist():
    global _blacklist_cache
    if _blacklist_cache is not None:
        return list(_blacklist_cache)
    path = _blacklist_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                _blacklist_cache = [int(x) for x in data]
            else:
                _blacklist_cache = []
    except Exception as e:
        print(f"[blacklist] load failed ({path}): {e}")
        _blacklist_cache = []
    return list(_blacklist_cache)

def save_blacklist(user_ids):
    global _blacklist_cache
    path = _blacklist_path()
    cleaned = [int(x) for x in user_ids]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2)
    _blacklist_cache = cleaned
    print(f"[blacklist] saved {len(cleaned)} ids -> {path}")

def add_blacklist_user(user_id: int) -> bool:
    bl = load_blacklist()
    uid = int(user_id)
    if uid in bl:
        return False
    bl.append(uid)
    save_blacklist(bl)
    return True

def remove_blacklist_user(user_id: int) -> bool:
    bl = load_blacklist()
    uid = int(user_id)
    if uid in bl:
        bl.remove(uid)
        save_blacklist(bl)
        return True
    return False

def is_blacklisted(user_id: int) -> bool:
    try:
        return int(user_id) in load_blacklist()
    except Exception:
        return False

async def hard_block_interaction(interaction: discord.Interaction) -> bool:
    """
    Return True if interaction must be blocked.
    Blocks:
      - blacklisted users (except whitelist owners)
      - ANY non-owner usage inside REQUIRED_GUILD_ID (your server)
    """
    uid = getattr(interaction.user, "id", None)
    if uid is None:
        return True
    try:
        wl = config.get("whitelist", []) if isinstance(config, dict) else []
        is_owner = int(uid) in [int(x) for x in wl]
    except Exception:
        is_owner = False

    # blacklist
    if not is_owner and is_blacklisted(uid):
        print(f"[gate] BLACKLIST hit {interaction.user} ({uid}) type={interaction.type}")
        try:
            msg = "**You are blacklisted from using this bot.**"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            print(f"[gate] blacklist deny failed: {e}")
        return True

    # own server lock — nobody except whitelist can do anything here
    try:
        gid = interaction.guild.id if interaction.guild else None
    except Exception:
        gid = None
    if REQUIRED_GUILD_ID and gid == REQUIRED_GUILD_ID and not is_owner:
        print(f"[gate] OWN-SERVER block {interaction.user} ({uid}) type={interaction.type}")
        try:
            msg = "**Commands are disabled in this server.**"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            print(f"[gate] own-server deny failed: {e}")
        return True

    return False


async def require_not_blacklisted(interaction: discord.Interaction) -> bool:
    """False = blocked (blacklist or own-server)."""
    if await hard_block_interaction(interaction):
        return False
    return True


LEADERBOARD_FILE = "leaderboard.json"

def _leaderboard_path():
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, LEADERBOARD_FILE)

def load_leaderboard():
    path = _leaderboard_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_leaderboard(data: dict):
    path = _leaderboard_path()
    try:
        # never store non-dict trash
        clean = {}
        for k, v in (data or {}).items():
            if isinstance(v, int):
                clean[str(k)] = {"overall": int(v)}
            elif isinstance(v, dict):
                clean[str(k)] = v
        with open(path, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2)
    except Exception as e:
        print(f"[leaderboard] save failed: {e}")

def export_leaderboard_public(data: dict | None = None):
    """Write website-friendly leaderboard_public.json (top users). Never overwrites full data."""
    if data is None:
        data = load_leaderboard()
    rows = []
    for uid, entry in (data or {}).items():
        if isinstance(entry, int):
            overall = int(entry)
            top_cmd, top_n = None, 0
        elif isinstance(entry, dict):
            overall = int(entry.get("overall", 0) or 0)
            top_cmd, top_n = None, 0
            for k, v in entry.items():
                if k == "overall":
                    continue
                try:
                    n = int(v)
                except Exception:
                    continue
                if n > top_n:
                    top_cmd, top_n = k, n
        else:
            continue
        rows.append({
            "id": str(uid),
            "overall": overall,
            "top_command": top_cmd,
            "top_command_count": top_n,
        })
    rows.sort(key=lambda r: r["overall"], reverse=True)
    payload = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "total_users": len(rows),
        "leaders": rows[:25],
    }
    public_path = os.path.join(os.path.dirname(_leaderboard_path()), "leaderboard_public.json")
    try:
        with open(public_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"[leaderboard] public export failed: {e}")
    return payload

def update_leaderboard(user_id: int, command_name: str = "unknown"):
    """Track command usage per user. Safe against old int-only format."""
    data = load_leaderboard()
    uid = str(int(user_id))
    entry = data.get(uid)

    # migrate legacy formats: int total OR missing overall
    if entry is None:
        entry = {"overall": 0}
    elif isinstance(entry, int):
        entry = {"overall": int(entry)}
    elif not isinstance(entry, dict):
        entry = {"overall": 0}

    entry["overall"] = int(entry.get("overall", 0)) + 1
    cmd = str(command_name or "unknown")
    entry[cmd] = int(entry.get(cmd, 0)) + 1
    data[uid] = entry
    save_leaderboard(data)
    export_leaderboard_public(data)

def get_leaderboard_top(limit: int = 10):
    data = load_leaderboard()
    rows = []
    for uid, entry in data.items():
        if isinstance(entry, int):
            overall = int(entry)
        elif isinstance(entry, dict):
            overall = int(entry.get("overall", 0))
        else:
            overall = 0
        rows.append((uid, overall, entry if isinstance(entry, dict) else {"overall": overall}))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows[: max(1, min(limit, 25))]

def save_token(token):
    with open("config.json", "w") as file:
        json.dump({"TOKEN": token}, file)

def load_token():
    try:
        with open("config.json", "r") as file:
            data = json.load(file)
            return data.get("TOKEN")
    except FileNotFoundError:
        print(Fore.RED + "Error: 2 not found.")
        return None
    except json.JSONDecodeError:
        print(Fore.RED + "Error: Invalid JSON format in config.json.")
        return None

logo = f"""{Fore.MAGENTA}

  ___ _  _ ___  ___  __  __ _  _ ___   _   
 |_ _| \\| / __|/ _ \\|  \\/  | \\| |_ _| /_\\  
  | || .` \\__ \\ (_) | |\\/| | .` || | / _ \\ 
 |___|_|\\_|___/\\___/|_|  |_|_|\\_|___/_/ \\_\\
{Fore.WHITE}     raiding made easy                        
 
"""



def display_status(connected):
    if connected:
        print(Fore.GREEN + "Status: Connected")
    else:
        print(Fore.RED + "Status: Disconnected")

def token_management():
    # Non-interactive for panels: env DISCORD_TOKEN > config.json TOKEN
    import os as _os
    env_tok = _os.getenv("DISCORD_TOKEN") or _os.getenv("TOKEN")
    if env_tok:
        print(Fore.GREEN + "Token loaded from environment.")
        return env_tok.strip()
    tok = load_token()
    if tok:
        print(Fore.GREEN + "Token loaded from config.json.")
        return tok
    # Fallback interactive (local only)
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
        print(Fore.CYAN + "Welcome to the bot token management!\n")
        print("1. Set new token")
        print("2. Load previous token")
        print()
        choice = input(f"{Fore.YELLOW}>{Fore.WHITE} Choose an option (1, 2){Fore.YELLOW}:{Fore.WHITE} ")
        if choice == "1":
            new_token = input(Fore.GREEN + "Enter the new token: ")
            save_token(new_token)
            print(Fore.GREEN + "Token successfully set!")
            return new_token
        token = load_token()
        if token:
            print(f"{Fore.GREEN}>{Fore.WHITE} Previous token loaded.")
            return token
        print(Fore.RED + "No token found.")
        return None
    except EOFError:
        print(Fore.RED + "No token in config.json / env and no interactive stdin.")
        return None


def load_presets():
    if not os.path.exists(PRESETS_FILE):
        return {}

    try:
        with open(PRESETS_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except json.JSONDecodeError:
        return {}

def save_preset(user_id, message):
    data = load_presets()
    data[str(user_id)] = message

    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def get_preset(user_id):
    data = load_presets()
    return data.get(str(user_id))

class PresetModal(Modal, title="Set Your Custom Raid Message"):
    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id
        self.message_input = TextInput(label="Enter your spam message", style=discord.TextStyle.long, max_length=2000)
        self.add_item(self.message_input)

    async def on_submit(self, interaction: Interaction):
        save_preset(self.user_id, self.message_input.value)
        await interaction.response.send_message("✅ Preset message saved successfully!", ephemeral=True)

class PresetView(View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    @discord.ui.button(label="Set Message", style=ButtonStyle.green)
    async def set_message(self, interaction: Interaction, button: Button):
        await interaction.response.send_modal(PresetModal(user_id=self.user_id))

    @discord.ui.button(label="Preview Message", style=ButtonStyle.primary)
    async def preview_message(self, interaction: Interaction, button: Button):
        message = get_preset(self.user_id)
        if message:
            await interaction.response.send_message(f"📄 **Your preset message:**\n```{message}```", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ No preset message found. Please set one first.", ephemeral=True)

@bot.tree.command(name="preset-message", description="Manage your custom raid message preset.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def preset_message(interaction: discord.Interaction):
    if not is_premium_user(interaction.user.id):
        await interaction.response.send_message("💎 This command is only available for premium users.", ephemeral=True)
        return
    view = PresetView(user_id=interaction.user.id)
    embed = discord.Embed(
        title="⚡ Preset Message",
        description="Use the buttons below to set or preview your raid message.",
        color=0xa874d1
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)



SPOOF_MAP = {
    "tiktok_video": "https://www.tiktok.com/@havoc/video/0000000000000000000",
    "tiktok_account": "https://www.tiktok.com/@havoc",
    "instagram_video": "https://www.instagram.com/reel/havoc000",
    "instagram_account": "https://www.instagram.com/havoc/",
    "roblox_account": "https://www.roblox.com/users/1/profile",
}

@bot.tree.command(
    name="createlogger",
    description="[💎] Create a redirect IPLogger shortlink (use for legal purposes only!)"
)
@app_commands.describe(
    domain="Choose the logger domain",
    destination="Target URL for the redirect (e.g., https://tiktok.com)",
    spoof_type="Choose how the link should appear"
)
@app_commands.choices(domain=[
    app_commands.Choice(name="ed.tc", value="ed.tc"),
    app_commands.Choice(name="wl.gl", value="wl.gl"),
    app_commands.Choice(name="bc.ax", value="bc.ax"),
])
@app_commands.choices(spoof_type=[
    app_commands.Choice(name="TikTok Video", value="tiktok_video"),
    app_commands.Choice(name="TikTok Account", value="tiktok_account"),
    app_commands.Choice(name="Instagram Video", value="instagram_video"),
    app_commands.Choice(name="Instagram Account", value="instagram_account"),
    app_commands.Choice(name="Roblox Account", value="roblox_account")
])
async def createlogger(
    interaction: discord.Interaction,
    domain: app_commands.Choice[str],
    destination: str,
    spoof_type: app_commands.Choice[str]
):
    await interaction.response.defer(ephemeral=True, thinking=True)

    if not is_premium_user(interaction.user.id):
        await interaction.followup.send("💎 This command is only available for premium users.", ephemeral=True)
        return

    try:
        if not destination.startswith(("http://", "https://")):
            destination = "https://" + destination

        payload = {
            "domain": domain.value,
            "alias": "discord_logger",
            "destination": destination
        }

        endpoint = "https://api.iplogger.org/create/shortlink/"

        response = requests.post(
            endpoint,
            headers={"X-token": IPLOGGER_API_KEY},
            data=payload
        )
        data = response.json()

        if "result" in data:
            shortlink = data["result"].get("shortlink")
            direct_link = f"https://{data['result']['domain']}/{data['result']['link']}"
            viewer_link = f"https://iplogger.org/logger/{data['result']['id']}"

            spoof_base = SPOOF_MAP[spoof_type.value]
            spoofed_link_msg = f"[{spoof_base}]({shortlink})"

            details_msg = (
                f"✅ **Logger created!**\n"
                f"🔗 **Public link:** {shortlink}\n"
                f"👁 **Dashboard link (dont share):** {viewer_link}\n"
                f"🎯 **Redirects to:** {payload['destination']}\n\n"
                f":exclamation: **Tip:** Forward the logger link above to the victim. Copy and pasting will break the spoofed link.\n"
            )

            try:
                await interaction.user.send(spoofed_link_msg)
                await interaction.user.send(details_msg)

                await interaction.followup.send("📩 Sent to your DMs!", ephemeral=True)

            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ I couldn't DM you! Please enable DMs from server members.",
                    ephemeral=True
                )

        else:
            await interaction.followup.send(f"❌ Invalid API response: {data}", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"⚠️ API request error: {e}", ephemeral=True)



class SpamButton(discord.ui.View):
    def __init__(self, message):
        super().__init__()
        self.message = message

    @discord.ui.button(label="Spam", style=discord.ButtonStyle.red)
    async def spam_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await hard_block_interaction(interaction):
            return
        await interaction.response.defer()
        allowed = discord.AllowedMentions(everyone=True, users=True, roles=True)
        for _ in range(5):  
            await interaction.followup.send(self.message, allowed_mentions=allowed)  

@bot.tree.command(name="custom-raid", description="[💎] Premium Raid with your own message. (premium only!)")
@app_commands.describe(message="Optional: your custom message to spam (use /preset-message if you want to save it)")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def custom_raid(interaction: discord.Interaction, message: str = None):
    if not is_premium_user(interaction.user.id):
        await interaction.response.send_message("💎 This command is only available for premium users.", ephemeral=True)
        return

    if not message:
        message = get_preset(interaction.user.id)
        if not message:
            await interaction.response.send_message("❌ You have not set a preset message. Use `/preset-message` to set one.", ephemeral=True)
            return

    view = SpamButton(message)
    await interaction.response.send_message(f"💎 SPAM TEXT:\n```{message}```", view=view, ephemeral=True)

    await log_command_use(
        user=interaction.user,
        command_name="💎 custom-raid",
        channel=interaction.channel,
        message=message
    )




class PingButton(discord.ui.View):
    def __init__(self, user_ids: list[str], pings_per_message: int = 1):
        super().__init__(timeout=None)
        self.user_ids = user_ids
        self.pings_per_message = pings_per_message
        self.delay = 1

    @discord.ui.button(label="🔁 Ping!", style=discord.ButtonStyle.red)
    async def ping_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await hard_block_interaction(interaction):
            return
        if not self.user_ids:
            await interaction.response.send_message("⚠️ No IDs available to ping.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        max_retries = 2

        for _ in range(5):
            selected_ids = random.sample(self.user_ids, min(self.pings_per_message, len(self.user_ids)))
            mentions = " ".join(f"<@{uid}>" for uid in selected_ids)
            pingmsg = '''
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
                                  ***@havoc**   `🌙`
                  raid b__o__t  ﹒ s__o__cial  ﹒ to__xic__
                         `🌟`     _join to [RAID](https://tenor.com/view/playboi-carti-discord-discord-raid-gif-21005635) any server __Without Admin perms__, free to use_ :moneybag: 

⠀⠀⠀⠀⠀⠀⠀                            **[JOIN](https://discord.gg/U3RussXskz) TODAY, AND R__AI__D EVER__Y__ SERVER YOU WANT WITHOUT [ADMIN](https://tenor.com/view/mooning-show-butt-shake-butt-pants-down-gif-17077775)**
            '''
            message_content = f"{mentions}\n{pingmsg}"
            retries = 0
            while retries <= max_retries:
                try:
                    await interaction.followup.send(message_content, ephemeral=False)
                    break
                except discord.errors.HTTPException as e:
                    if e.status == 429:
                        retry_after = getattr(e, "retry_after", 1.5)
                        retry_after = min(retry_after, 5)
                        print(f"Rate limit hit, retrying after {retry_after:.2f}s (retry {retries + 1}/{max_retries})")
                        await asyncio.sleep(retry_after)
                        retries += 1
                    else:
                        raise e
            else:
                print("Failed to send message after max retries, skipping.")


@bot.tree.command(name="ping", description="Ping random user IDs from a .txt file using a button.")
@app_commands.describe(
    file="A .txt file containing user IDs (one per line)",
    pings_per_message="amount of users to ping per message (most servers block 5+ pings per message so keep it low)"
)
@app_commands.rename(pings_per_message="amount")
async def ping_from_file(
    interaction: discord.Interaction,
    file: discord.Attachment,
    pings_per_message: int = 1
):

    try:
        if not file.filename.endswith(".txt"):
            await interaction.response.send_message("❌ Please upload a valid `.txt` file with user IDs.", ephemeral=True)
            return

        file_content = await file.read()
        text = file_content.decode("utf-8")
        user_ids = [line.strip() for line in text.splitlines() if line.strip().isdigit()]

        if not user_ids:
            await interaction.response.send_message("⚠️ No valid user IDs found in the file.", ephemeral=True)
            return

        view = PingButton(user_ids, pings_per_message)
        await interaction.response.send_message("🔴 Click to ping random users!", view=view, ephemeral=True)


    except Exception as e:
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ Error: `{e}`", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Error: `{e}`", ephemeral=True)




class AvatarView(discord.ui.View): # made that shit in 5min its really ass
    def __init__(self, user: discord.User, banner_url: str = None):
        super().__init__()
        avatar_url = user.display_avatar.url

        self.add_item(discord.ui.Button(label="Download Avatar as JPG", url=avatar_url + "?format=jpg"))
        self.add_item(discord.ui.Button(label="Download Avatar as PNG", url=avatar_url + "?format=png"))

        if banner_url:
            self.add_item(discord.ui.Button(
                label="View Banner",
                style=discord.ButtonStyle.blurple, 
                url=banner_url
            ))
            self.add_item(discord.ui.Button(label="Download Banner as JPG", url=banner_url + "?format=jpg"))
            self.add_item(discord.ui.Button(label="Download Banner as PNG", url=banner_url + "?format=png"))

class AvatarView(discord.ui.View):
    def __init__(self, user: discord.User, banner_url: str = None):
        super().__init__()
        avatar_url = user.display_avatar.url

        self.add_item(discord.ui.Button(label="Download Avatar", url=avatar_url + "?format=png"))

        if banner_url:
            self.add_item(discord.ui.Button(label="Download Banner", url=banner_url + "?format=png"))

@bot.tree.command(name="avatar", description="Get a user's avatar and banner.")
@app_commands.describe(user="The user whose avatar you want to see")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def avatar(interaction: discord.Interaction, user: discord.User = None):
    user = user or interaction.user

    full_user = await interaction.client.fetch_user(user.id)
    banner_url = full_user.banner.url if full_user.banner else None

    embed = discord.Embed(
        title=f"{user.display_name}'s Avatar & Banner",
        color=0xa874d1
    )
    
    embed.set_thumbnail(url=user.display_avatar.url)

    if banner_url:
        embed.set_image(url=banner_url)

    embed.set_footer(
        text=f"Requested by {interaction.user.display_name}",
        icon_url=interaction.client.user.avatar.url if interaction.client.user.avatar else None
    )

    view = AvatarView(user, banner_url)

    await interaction.response.send_message(embed=embed, view=view)

    await log_command_use(
        user=interaction.user,
        command_name="avatar",
        channel=interaction.channel,
        message=user.display_avatar.url
    )


class FloodButton(discord.ui.View):
    def __init__(self, message, delay):
        super().__init__()
        self.message = message
        self.delay = delay

    @discord.ui.button(label="⚡ Execute Command", style=discord.ButtonStyle.blurple)
    async def flood_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await hard_block_interaction(interaction):
            return
        await interaction.response.defer()
        max_retries = 2

        for _ in range(5):
            retries = 0
            while retries <= max_retries:
                try:
                    await interaction.followup.send(self.message, allowed_mentions=discord.AllowedMentions(everyone=True))
                    await asyncio.sleep(self.delay + random.uniform(0.1, 0.5))
                    break
                except discord.errors.HTTPException as e:
                    if e.status == 429:
                        retry_after = getattr(e, "retry_after", 1.5)
                        retry_after = min(retry_after, 5)
                        print(f"{Fore.YELLOW}>{Fore.WHITE} Rate limit hit, retrying after {Fore.YELLOW}{retry_after:.2f}s{Fore.WHITE} (retry {Fore.YELLOW}{retries + 1}{Fore.WHITE}/{Fore.YELLOW}{max_retries}{Fore.WHITE})")
                        await asyncio.sleep(retry_after)
                        retries += 1
                    else:
                        raise e
            else:
                print(f"{Fore.RED}>{Fore.WHITE} Failed to send message after max retries, skipping{Fore.RED}.{Fore.WHITE}")



class IPView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

@bot.tree.command(name="ip", description="Reveal a user's IP to scare them! (fake)")
@app_commands.describe(user="The user you want to trace")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def ip(interaction: discord.Interaction, user: discord.User):
    fake_ip = ".".join(str(random.randint(1, 255)) for _ in range(4))
    port = random.choice([22, 443, 8080])
    trace_id = f"#ZTA-{random.randint(1000, 9999)}"

    embed = discord.Embed(
        title="🚨 CRITICAL: Unauthorized Network Access Detected",
        description=(
            f"Intrusion Detection System has traced your connection: **IP {fake_ip}, Port {port}**, Subnet **255.255.255.0**.\n"
            f"Your activity has been flagged as a potential security breach and logged for further analysis. "
            f"Cease unauthorized actions immediately or face escalation.\n\n"
            f"🔒 **Security Alert**\n"
            f"Your IP address has been identified as: **{fake_ip}**. This information has been logged for security monitoring.\n\n"
            f"**Threat Level**: HIGH\n"
            f"**Trace ID**: `{trace_id}`\n"
            f"**Timestamp**: {discord.utils.format_dt(interaction.created_at, style='F')}"
        ),
        color=discord.Color.red()
    )

    await interaction.response.send_message("🔍 Tracing IP...", ephemeral=True)

    await interaction.followup.send(
        content=f"{user.mention}",
        embed=embed,
        view=IPView()
    )
    await log_command_use(interaction.user, "ip reveal")

@ip.error
async def ip_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.TransformError):
        await interaction.response.send_message("User not found. Please mention a valid member.", ephemeral=True)
    else:
        await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)

import base64


def get_badges(user: discord.Member) -> str:
    flags = user.public_flags
    badges = []

    if flags.hypesquad: badges.append("🏠 HypeSquad")
    if flags.hypesquad_bravery: badges.append("🦁 Bravery")
    if flags.hypesquad_brilliance: badges.append("🧠 Brilliance")
    if flags.hypesquad_balance: badges.append("⚖️ Balance")
    if flags.early_supporter: badges.append("🌟 Early Supporter")
    if flags.staff: badges.append("👔 Staff")
    if flags.partner: badges.append("🤝 Partner")
    if flags.verified_bot: badges.append("🤖 Verified Bot")
    if flags.verified_bot_developer: badges.append("👨‍💻 Bot Dev")

    return ", ".join(badges) if badges else "No Badges"

@bot.tree.command(name="hack", description="Hack to scare them! (fake)")
@app_commands.describe(user="The user you want to hack")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def hack(interaction: discord.Interaction, user: discord.User):
    user_id_str = str(user.id)
    b64_id = base64.b64encode(user_id_str.encode()).decode()[:-2]

    badges = get_badges(user)

    file_options = [
        "stealer_base_23.04.2025.txt",
        "stealer_base_11.10.2022.db",
        "stealer_logs_240520.txt",
        "stealer_base202401.db",
        "breach_base_01_03_2021.txt",
        "breach_logs_2025.txt"
        "stealer_base_23.04.2025.txt",
        "stealer_base_11.10.2022.db",
        "stealer_logs_240520.txt",
        "stealer_base202401.db",
        "breach_base_01_03_2021.txt",
        "breach_logs_2025.txt",
        "stealer_backup_15.08.2023.db",
        "breach_archive_202212.txt",
        "stealer_data_03122024.db",
        "breach_base_99_99_9999.txt",
        "stealer_records_07.07.2020.txt",
        "logs_stealer_202503.db",
        "breach_dump_12_12_2022.txt",
        "stealer_cache_20240115.db",
        "breach_data_2025_backup.txt",
        "stealer_base_old_201901.db"
    ]
    found_in_file = random.choice(file_options)

    embed = discord.Embed(
        title=f"Found in: {found_in_file}",
        color=discord.Color.purple()
    )

    embed.add_field(
        name=f"{user.name} ({user.id})",
        value=(
            f"🪙 **Token:**\n`{b64_id}****`\n\n"
            f":e_mail:  Gmail: `Hidden`\n"
            f":iphone: Phone: `Hidden`\n"
            f":globe_with_meridians: Earth IP: `Hidden`"
        ),
        inline=False
    )

    embed.add_field(name="🎖 Badges:", value=badges, inline=True)
    embed.add_field(name="💳 Billing:", value="`(no billing)`", inline=True)
    embed.add_field(name="👥 HQ Friends:", value="`None`", inline=True)
    embed.add_field(name="🌍 Guilds:", value="`None`", inline=True)
    embed.add_field(name="🎁 Gift codes:", value="`None`", inline=True)

    embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
    embed.set_footer(text="havoc")

    await interaction.response.send_message(":computer: breaching account...", ephemeral=True)

    await interaction.followup.send(
        content=f"{user.mention}",
        embed=embed,
        view=IPView()
    )


@hack.error
async def hack_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.TransformError):
        await interaction.response.send_message("User not found. Please mention a valid member.", ephemeral=True)
    else:
        await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)



RAGEBAIT = ["""
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
# JOIN HAVOC AND START RAIDING
@everyone
discord.gg/U3RussXskz
https://tenor.com/view/mooning-show-butt-shake-butt-pants-down-gif-17077775
https://media.discordapp.net/attachments/1215053612028526653/1219435249763750028/1218622476645564527_1650x1080.gif?ex=686c5f93&is=686b0e13&hm=1f0bd7f260f88162001a02772b415d14168a43cf7ee7cc94c2c9f03af54d9bed&
    """,
    """
# YOU HAVE BEEN RAIDED BY [HAVOC 🆘](https://tenor.com/view/mooning-show-butt-shake-butt-pants-down-gif-17077775)
# RAID ANY SERVER WITHOUT ADMIN PERMS 🔐
# FREE, EASY TO USE, UP 24/7
# ANONYMOUSLY RAID ANY SERVER YOU WANT
# "IF YOU CANT BEAT THEM, [JOIN](https://discord.gg/U3RussXskz) THEM! @everyone"
⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀                            [JOIN HAVOC, RAID ANY SERVER YOU WANT, ANYTIME, ANYWHERE, ANYWHERE](https://discord.gg/U3RussXskz)
 
[穹忩犈垃箚泗趨菋纳攇幀驼懅七](https://cdn.discordapp.com/attachments/1153733814732992573/1166450104350290020/d480327590432d30f979d4ce46baea6b.gif?ex=686e1290&is=686cc110&hm=312bf638b772621b7e9f33ac2f62832c5d417a7dbd08a307d5ae94e96cc9d8d1&)
    """,
    """
# [HAVOC](https://discord.gg/U3RussXskz) OWNS ME AND ALL :zany_face: 
# GET RAIDED U BRAINDEAD NIGGERS :rofl: :rofl: :rofl:
# IMAGINE U CANT SETUP A SERVER LMAOOOO
# BETTER [JOIN](https://discord.gg/U3RussXskz) HAVOC AND START RAIDING U TWAT 
https://tenor.com/view/cat-hacking-silly-cat-hacker-cat-hacker-gif-14852445362476137270
[穹忩犈垃箚泗趨菋纳攇幀驼懅七](https://cdn.discordapp.com/attachments/1153733814732992573/1166450104350290020/d480327590432d30f979d4ce46baea6b.gif?ex=686e1290&is=686cc110&hm=312bf638b772621b7e9f33ac2f62832c5d417a7dbd08a307d5ae94e96cc9d8d1&)
@everyone
    """,
    """
# [HAVOC](https://tenor.com/view/flashbang-guy-screaming-guy-getting-flashbang-blinded-blinding-gif-1425127881206275521) __DOMINATES__ ALL 👑
# GET __RAIDED__, YOU RETARDS CAN'T HANDLE THIS 😭 🥀 🥀
# IMAGINE NOT BEING ABLE TO SETUP A SERVER LMAO
# BETTER [JOIN](https://discord.gg/U3RussXskz) HAVOC AND START RAIDING, YOU KNOW YOU WANT TO!
@everyone
    """
]


SCARY = [
    """
    # [HAVOC](https://media.tenor.com/uw5s-aHlviAAAAAM/scary-ghost.gif)
    # [HAVOC](https://discord.gg/U3RussXskz)
    # [HAVOC](https://tenor.com/view/yapping-creepy-under-the-bed-talking-ghost-gif-10296050582380126660)
    # [HAVOC](https://cdn.discordapp.com/attachments/1416037733322719364/1418258241879539733/RussianSleepExperimentGuy.png?ex=68cd776a&is=68cc25ea&hm=4141a571871aebcf5e93aa57d505285a924103536892e8a5b3ff0636c7ff2590&)
    @everyone
    """,
    """
    # [HAVOC](https://media.tenor.com/HMtY33kDWFwAAAAM/donk.gif)
    # [HAVOC](https://nightmarenostalgia.com/wp-content/uploads/2023/07/main-qimg-522ae83e590c80bfaf895b3919462bcb.gif?w=480)
    # [HAVOC](https://media.tenor.com/ihDOwbsgwRcAAAAM/scary-scary-face.gif)
    # [HAVOC](https://discord.gg/U3RussXskz)
    @everyone
    """
]

ASCII = [
    r"""
```

  /$$                                                       /$$                
 |__/                                                      |__/                
  /$$ /$$$$$$$   /$$$$$$$  /$$$$$$  /$$$$$$/$$$$  /$$$$$$$  /$$  /$$$$$$       
 | $$| $$__  $$ /$$_____/ /$$__  $$| $$_  $$_  $$| $$__  $$| $$ |____  $$      
 | $$| $$  \ $$|  $$$$$$ | $$  \ $$| $$ \ $$ \ $$| $$  \ $$| $$  /$$$$$$$      
 | $$| $$  | $$ \____  $$| $$  | $$| $$ | $$ | $$| $$  | $$| $$ /$$__  $$      
 | $$| $$  | $$ /$$$$$$$/|  $$$$$$/| $$ | $$ | $$| $$  | $$| $$|  $$$$$$$      
 |__/|__/  |__/|_______/  \______/ |__/ |__/ |__/|__/  |__/|__/ \_______/      
                                                          
```
***BETTER [JOIN](https://discord.gg/U3RussXskz) HAVOC AND START RAIDING***
[HAVOC ON TOP](https://tenor.com/view/shawn-breezy-gamma-male-gif-13452613280176262444)
@everyone

    
    """,
    r"""
```
.__                                   .__         
|__| ____   __________   _____   ____ |__|____    
|  |/    \ /  ___/  _ \ /     \ /    \|  \__  \   
|  |   |  \\___ (  <_> )  Y Y  \   |  \  |/ __ \_ 
|__|___|  /____  >____/|__|_|  /___|  /__(____  / 
        \/     \/            \/     \/        \/
                                 havoc on top
```
***[JOIN](https://discord.gg/U3RussXskz) HAVOC AND START RAIDING TODAY***
***[FREE](https://havoc.top/) TO USE, NO PERMS NEEDED***
@everyone

    """,
    r"""
```diff
-██╗███╗   ██╗███████╗ ██████╗ ███╗   ███╗███╗   ██╗██╗ █████╗     
-██║████╗  ██║██╔════╝██╔═══██╗████╗ ████║████╗  ██║██║██╔══██╗    
-██║██╔██╗ ██║███████╗██║   ██║██╔████╔██║██╔██╗ ██║██║███████║    
-██║██║╚██╗██║╚════██║██║   ██║██║╚██╔╝██║██║╚██╗██║██║██╔══██║    
-██║██║ ╚████║███████║╚██████╔╝██║ ╚═╝ ██║██║ ╚████║██║██║  ██║    
-╚═╝╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═══╝╚═╝╚═╝  ╚═╝    
                                                               
```
***[JOIN](https://discord.gg/U3RussXskz) HAVOC AND START RAIDING TODAY***
***[FREE](https://tenor.com/view/discord-discordgifemoji-red-blink-gif-13138334) TO USE, NO PERMS NEEDED***
@everyone
    """
]

HENTAI = [
    """
⢠⣾⣿⣿⣿⠄⢻⣿⣿⣿⡇⢰⣿⣿⣬⣭⣅⠊⢻⡇⢰⣿⡆⠄⠄
⢿⣿⣿⣿⣿⠄⢸⣿⠟⢛⡄⢸⣿⣿⣦⡁⢿⣷⣮⡃⠟⢿⣿⡀⠄
⢀⣿⣿⣿⣿⡇⠘⢣⣾⠟⠄⠸⣿⣿⣿⣿⣦⢹⣿⣿⣦⡑⠈⠁⠄
⢸⣿⣿⣿⣿⡇⢠⠟⠁⠾⡏⠄⠘⠻⣿⣿⣿⢸⣿⣿⣿⠿⠟⠛⡄
⠘⣿⣿⣿⣿⣿⠈⠄⣄⡀⠄⠂⢲⡦⡈⢻⡿⢸⣿⠿⣫⣴⠶⠶⣻
⠈⠛⠿⢿⣿⣧⣠⢝⠓⠄⠠⢅⡠⠤⠒⡐⠲⢶⣤⣤⣤⣤⠔⠁ ⠄
⠄⠄⢀⣀⠇⠙⠊⠉⢸⣿⣿⣿⣿⣿⣿⣿⣿⣶⠖⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⢀⣠⣿⣿⣿⣿⣿⣿⣿⣿⣿⡟⠄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⣠⠸⣿⣿⣿⣿⣿⣿⣿⣿⢿⣿⣿⣷⠄⠄⠄⠄⠄⠄
⠄⠄⢠⣴⣿⣿⣷⣦⡙⣿⣿⣿⣿⣿⣿⣼⣿⣿⣿⣷⣄⠄⠄⠄⠄
⠄⣴⣿⣿⣿⣿⣿⣶⣭⡀⠻⣿⣿⣿⣿⣿⣿⣿⠟⣭⣶⣷⣄⠄⠄
⣸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦⡌⠙⠛⠛⠛⢋⣵⣿⣿⣿⣿⣿⣷⠄
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣾⠛⣛⣴⣿⣿⣿⣿⣿⣿⣿⣿⣷

# [Uwu](https://discord.gg/U3RussXskz) G-G-GET (uwu) W-W-WAIDED (˘³˘) B-B-BY ˚(ꈍ ω ꈍ).₊̣̇. I-I-I-[INSOMNYIA](https://pa1.aminoapps.com/5985/ded984459526799715a26557194711a049e81c6e_hq.gif) (◡ ꒳ ◡)
@everyone
    """,
    """
⠄⢹⡄⠄⢸⠄⠄⠄⠄⠄⢁⢿⣿⡋⣩⣍⢙⣿⣽⠅⠄⠄⠄⠄⠄⡿⠄⢀⡟⠄
⠄⠄⢷⡀⣿⣆⣠⣤⠠⢴⣦⣝⠻⣿⣿⣿⡿⢟⡡⠄⠄⢠⣄⡀⢀⡿⢀⡾⠄⠄
⠄⢠⣬⣷⡾⣏⠻⣿⣧⢁⠈⢿⢧⣀⡁⠁⠡⠞⠅⢀⢢⣿⣿⡿⣼⣷⣾⣣⣄⡀
⠘⢷⡛⠯⠿⣿⣶⠹⢫⣫⣿⣦⣊⡂⢀⡄⢀⣤⣶⣷⡳⡋⠉⢾⡿⠿⠯⠿⢺⠇
⠄⠘⠶⣷⣼⡿⠇⠄⢰⣿⣿⣿⣿⣿⣶⣶⣿⣿⣿⣿⣿⡹⡄⠘⠻⣧⣾⠟⠁⠄
⠄⠄⣼⣾⠟⠄⣀⣤⣾⣿⣿⣿⠛⢿⣿⡟⠛⣹⣹⣿⣿⣷⣦⡄⠐⣿⣿⣷⠄⠄
⠄⣜⡿⣩⡖⣰⣿⣿⣿⣿⣿⣿⣦⡀⠘⠄⣼⣿⣿⣿⣿⣿⣿⣿⡌⢾⡿⣿⣇⠄
⣼⠏⣴⠏⢰⣿⣿⣿⣿⣿⣿⣿⣿⣿⡆⣼⣿⣿⣿⣿⣿⣿⣿⣿⡇⠘⣿⡿⣿⣦
⡏⢰⠏⠄⢸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣧⣿⣿⣿⣿⣿⣿⣿⣿⣿⣇⠄⢸⣿⢿⣿

# I-I-INSOMNYIA O-O-ON (˘ω˘) T-T-TOP U-u-u [UwU](https://discord.gg/U3RussXskz) F-F-FUCKING (。U ω U。) [N-N-N-NIGGERS](https://tophentaicomics.com/wp-content/uploads/2020/03/delicious-hentai-gif-xxx-1584367984kn4g8.gif)
@everyone
    """,
    """
⠄⠄⡠⠺⠁⠄⠄⠈⠑⢦⠄
⠄⡜⠸⢰⡐⠄⠄⠄⠄⠄⣇
⠄⣯⡏⣘⣎⣂⣵⢀⢾⡄⡼
⠄⠏⣎⠟⣻⣿⢻⠃⢈⡝
⠄⠄⠹⠋⢉⣵⣮⣰⡚
⠄⠄⠄⠄⠸⣿⣿⡏⣷⢹⣦
⠄⠄⠄⢀⡄⣿⣿⡇⣾⡏⣻⡄
⠄⠄⢴⣿⣿⢹⣿⡇⣿⣧⢿⣇
⠄⠸⣸⣿⣿⢸⣿⡇⣿⣿⣟⢿⣦⣀
⠄⠄⠈⠛⠛⠈⣿⣷⢻⡿⢟⣣⣭⣭⣝⡲⢶⣶⣤⣄⡀
⠄⠄⠄⠄⠄⠸⣿⢟⣤⣾⣿⣿⣿⣿⣿⣿⣷⡹⣿⣿⣿⣷⣄
⠄⠄⠄⠄⠄⢀⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇⢻⣿⣿⣿⣿⣆
⠄⠄⠄⢀⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠱⡜⣿⣿⣿⣿⡿⣾⣷⠄
⠄⣠⣶⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⢛⣵⠇⡇⣿⣿⣿⢟⣵⢸⣿⡇
⣼⣿⣭⣶⣶⣶⣶⣝⡻⣿⣿⡿⠿⡛⠁⠄⠁⠄⠄⠄⠄⠄⠄⣵⣿⣿⠟
⠹⣿⣿⣿⣿⣿⣿⣿⣿⣶⣶⣴⡸⣿⣧⣀⡤⣤⠄⠄⠄⠄⠄⢷⢰⠞⠄
    
# J-J-J-JOIN I-I-[INSOMNYIA](https://66.media.tumblr.com/43763839ac3e228314a43a0ffcced591/tumblr_p3jog4Xk5g1x09foko1_400.gif) x3 A-A-A-AND S-S-STAWT W-W-WAIDING :3 T-T-T-TODAY
# NYO P-P-P-PEWMS uwU N-N-NYEEDED, (U ﹏ U) F-F-F-FWEE T-T-TO U-U-U-USE [(⑅˘꒳˘)](https://discord.gg/U3RussXskz)
@everyone
    """
]

class BspamButton(discord.ui.View):
    def __init__(self, spam_texts, delay):
        super().__init__(timeout=900)
        self.spam_texts = spam_texts
        self.delay = delay

    @discord.ui.button(label="🚨 Spam Button", style=discord.ButtonStyle.danger)
    async def start_spam(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await hard_block_interaction(interaction):
            return
        await interaction.response.defer()
        for _ in range(20):
            random_text = random.choice(self.spam_texts)
            await interaction.followup.send(random_text, allowed_mentions=discord.AllowedMentions(everyone=True))
            await asyncio.sleep(self.delay)



@bot.tree.command(name="b-spam", description="Spam random messages with different styles.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    style="Choose spam style (ragebait, scary, ascii, hentai)",
    delay="Delay between messages (0.01 to 5.00 seconds)."
)
async def bspam(interaction: discord.Interaction, style: str, delay: float = 0.5):
    if delay < 0.01 or delay > 5.00:
        await interaction.response.send_message(
            "**Error: Delay must be between 0.01 and 5.00 seconds.**",
            ephemeral=True
        )
        return

    style = style.lower()
    if style == "ragebait":
        spam_list = RAGEBAIT
    elif style == "scary":
        spam_list = SCARY
    elif style == "ascii":
        spam_list = ASCII
    elif style == "hentai":
        spam_list = HENTAI
    else:
        await interaction.response.send_message("❌ Invalid style! Choose `standart`, `scary` or `ascii`.", ephemeral=True)
        return

    view = BspamButton(spam_list, delay)
    await interaction.response.send_message(
        f"🚨 Press the button to start spamming\n mode: **{style.upper()}**",
        view=view,
        ephemeral=True
    )



@bspam.autocomplete("style")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def style_autocomplete(interaction: discord.Interaction, current: str):
    styles = ["ragebait", "scary", "ascii", "hentai"]
    return [
        app_commands.Choice(name=s, value=s)
        for s in styles if current.lower() in s
    ]


@bot.tree.command(name="a-raid", description="RAID Any Server.")
@app_commands.describe(delay="Delay between messages in seconds (0.01 to 5.00).")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.user_install()
async def araid(interaction: discord.Interaction, delay: float = 0.01):
    if delay < 0.01 or delay > 5.00:
        await interaction.response.send_message("**Error: Delay must be between 0.01 and 5.00 seconds.**", ephemeral=True)
        return

    raid_message = '''
    ⠀⠀⠀⠀
⠀⠀⠀⠀
     
                                  ***@havoc**   `🌙`
                  raid b__o__t  ﹒ s__o__cial  ﹒ to__xic__
                         `🌟`     _join to [RAID](https://tenor.com/view/playboi-carti-discord-discord-raid-gif-21005635) any server __Without Admin perms__, free to use_ :moneybag: 
 
⠀⠀⠀⠀⠀⠀⠀                            **[JOIN](https://discord.gg/U3RussXskz) TODAY, AND R__AI__D EVER__Y__ SERVER YOU WANT WITHOUT [ADMIN](https://tenor.com/view/mooning-show-butt-shake-butt-pants-down-gif-17077775)** @everyone
    '''
    try:
        view = FloodButton(raid_message, delay)
        await interaction.response.send_message("Press the button to start raiding.", view=view, ephemeral=True)
    except discord.HTTPException as e:
        if e.code == 40094:  # follow-up message limit reached
            print(f"[A-RAID ERROR] Max follow-up messages reached for interaction {interaction.id}")
        else:
            print(f"[A-RAID ERROR] Unexpected HTTPException: {e}")
            raise

    await log_command_use(
        user=interaction.user,
        command_name="a-raid",
        channel=interaction.channel
    )



@bot.tree.command(name="say", description="Make the bot say something you want, anonymously.")
@app_commands.describe(message="The message you want the bot to say.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.user_install()
async def say(interaction: discord.Interaction, message: str):
    if is_premium_user(interaction.user.id):
        full_message = f"{message}"
    else:
        full_message = f"{message} \n\n discord.gg/U3RussXskz"

    await interaction.response.send_message("Sending.. 🔊", ephemeral=True)
    allowed = discord.AllowedMentions(everyone=True, users=True, roles=True)
    await interaction.followup.send(full_message, allowed_mentions=allowed)

    await log_command_use(
        user=interaction.user,
        command_name="say",
        message=message,
        channel=interaction.channel
    )


@bot.tree.command(
    name="ghostping",
    description="GhostPing Somebody multiple times! The best delay is 0.3 seconds"
)
@app_commands.describe(
    user="📔 The user you want to ghost ping",
    seconds="🕰️ The delay (in seconds) before each message is deleted. Best is 0.3 🕰️",
    times="🔁 How many times to ghost ping them 🔁"
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.user_install()
async def ghostping(
    interaction: discord.Interaction,
    user: discord.User,
    seconds: float = 0.3,
    times: int = 3
):
    await interaction.response.send_message("Ghost pinging...", ephemeral=True)
    await log_command_use(interaction.user, "ghostping")

    for i in range(times):
        try:
            message = await interaction.followup.send(f"{user.mention}")
            await asyncio.sleep(seconds)
            await message.delete()
        except discord.HTTPException as e:
            if e.code == 40094:  
                print(f"[ghostping] follow up messages reached – stopped after {i} pings.")
                break
            else:
                raise



@bot.tree.command(name="leaderboard", description="Show the top Havoc command users.")
@app_commands.describe(limit="How many users to show (1-15)")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def leaderboard_cmd(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 15] = 10):
    rows = get_leaderboard_top(limit)
    if not rows:
        await interaction.response.send_message("Leaderboard is empty — run some commands first.", ephemeral=True)
        return

    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, overall, entry) in enumerate(rows, start=1):
        prefix = medals[i-1] if i <= 3 else f"`#{i}`"
        # best single command besides overall
        top_cmd = None
        top_cmd_n = 0
        if isinstance(entry, dict):
            for k, v in entry.items():
                if k == "overall":
                    continue
                try:
                    n = int(v)
                except Exception:
                    continue
                if n > top_cmd_n:
                    top_cmd_n = n
                    top_cmd = k
        extra = f" · top: `/{top_cmd}` ×{top_cmd_n}" if top_cmd else ""
        lines.append(f"{prefix} <@{uid}> — **{overall}** uses{extra}")

    embed = discord.Embed(
        title="🌙 Havoc Leaderboard",
        description="\n".join(lines),
        colour=0xa874d1,
        timestamp=datetime.utcnow(),
    )
    embed.set_footer(text="Tracked from command usage")
    await interaction.response.send_message(embed=embed, ephemeral=False)
    await log_command_use(
        user=interaction.user,
        command_name="leaderboard",
        channel=interaction.channel,
        message=f"limit={limit}",
    )


def _is_owner(user_id: int) -> bool:
    try:
        wl = config.get("whitelist", []) if isinstance(config, dict) else []
        return int(user_id) in [int(x) for x in wl]
    except Exception:
        return False


def _parse_user_id(raw: str):
    """Accept raw id, <@id>, or <@!id>."""
    if raw is None:
        return None
    s = str(raw).strip()
    m = re.search(r"\d{15,20}", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


whitelist = config.get("whitelist", [])

@bot.tree.command(name="x-add-premium", description="Grant premium access to a user. (owner only)")
@app_commands.describe(user="The user to grant premium access to")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def add_premium(interaction: discord.Interaction, user: discord.User):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    add_premium_user(user.id)
    await interaction.response.send_message(f"✅ {user.mention} has been granted premium access!", ephemeral=True)

@bot.tree.command(name="x-rem-premium", description="Remove premium access from a user. (owner only)")
@app_commands.describe(user="The user to remove premium access from")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def rem_premium(interaction: discord.Interaction, user: discord.User):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    removed = remove_premium_user(user.id)
    if removed:
        await interaction.response.send_message(f"✅ User {user.mention} has been removed from premium access!", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ User {user.mention} does not have premium access.", ephemeral=True)

@bot.tree.command(name="x-blacklist", description="Blacklist a user from using the bot. (owner only)")
@app_commands.describe(
    user="User to blacklist (pick from list)",
    user_id="Or paste a user ID if they are not cached",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def x_blacklist(
    interaction: discord.Interaction,
    user: discord.User = None,
    user_id: str = None,
):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    target_id = None
    mention = None
    if user is not None:
        target_id = int(user.id)
        mention = user.mention
    elif user_id:
        target_id = _parse_user_id(user_id)
        mention = f"`{target_id}`" if target_id else None

    if not target_id:
        await interaction.response.send_message(
            "⚠️ Provide a `user` or a valid `user_id`.",
            ephemeral=True,
        )
        return

    if _is_owner(target_id):
        await interaction.response.send_message("⚠️ You cannot blacklist a whitelist owner.", ephemeral=True)
        return

    added = add_blacklist_user(target_id)
    # force reload so gate sees it immediately
    global _blacklist_cache
    _blacklist_cache = None
    load_blacklist()

    if added:
        await interaction.response.send_message(
            f"🚫 Blacklisted {mention} (`{target_id}`). They are blocked from all commands/buttons now.",
            ephemeral=True,
        )
        print(f"[blacklist] + {target_id} by {interaction.user.id}")
    else:
        await interaction.response.send_message(
            f"⚠️ `{target_id}` is already blacklisted.",
            ephemeral=True,
        )

@bot.tree.command(name="x-unblacklist", description="Remove a user from the blacklist. (owner only)")
@app_commands.describe(
    user="User to unblacklist",
    user_id="Or paste a user ID",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def x_unblacklist(
    interaction: discord.Interaction,
    user: discord.User = None,
    user_id: str = None,
):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    target_id = None
    mention = None
    if user is not None:
        target_id = int(user.id)
        mention = user.mention
    elif user_id:
        target_id = _parse_user_id(user_id)
        mention = f"`{target_id}`" if target_id else None

    if not target_id:
        await interaction.response.send_message(
            "⚠️ Provide a `user` or a valid `user_id`.",
            ephemeral=True,
        )
        return

    removed = remove_blacklist_user(target_id)
    global _blacklist_cache
    _blacklist_cache = None
    load_blacklist()

    if removed:
        await interaction.response.send_message(
            f"✅ Removed {mention} (`{target_id}`) from the blacklist.",
            ephemeral=True,
        )
        print(f"[blacklist] - {target_id} by {interaction.user.id}")
    else:
        await interaction.response.send_message(
            f"⚠️ `{target_id}` is not blacklisted.",
            ephemeral=True,
        )

@bot.tree.command(name="x-lb-export", description="Rebuild public leaderboard JSON for the website. (owner only)")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def x_lb_export(interaction: discord.Interaction):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ Owner only.", ephemeral=True)
        return
    payload = export_leaderboard_public()
    await interaction.response.send_message(
        f"✅ Public leaderboard exported (`{len(payload.get('leaders', []))} leaders`).\nFile: `leaderboard_public.json`",
        ephemeral=True,
    )


@bot.tree.command(name="x-blacklist-list", description="Show all blacklisted user IDs. (owner only)")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def x_blacklist_list(interaction: discord.Interaction):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    # always refresh from disk for the list view
    global _blacklist_cache
    _blacklist_cache = None
    bl = load_blacklist()
    if not bl:
        await interaction.response.send_message("Blacklist is empty.", ephemeral=True)
        return
    lines = "\n".join(f"`{uid}`" for uid in bl)
    if len(lines) > 1800:
        lines = lines[:1800] + "\n..."
    await interaction.response.send_message(
        f"**Blacklisted users ({len(bl)}):**\n{lines}\n\nFile: `{_blacklist_path()}`",
        ephemeral=True,
    )



class RoastButton(discord.ui.View):
    def __init__(self, user: discord.User, delay: float = 0.5):
        super().__init__()
        self.user = user
        self.delay = delay

    @discord.ui.button(label="⚡ Send Roast", style=discord.ButtonStyle.blurple)
    async def roast_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await hard_block_interaction(interaction):
            return
        await interaction.response.defer()
        max_retries = 2

        try:
            with open("roasts.txt", "r", encoding="utf-8") as f:
                roasts = [line.strip() for line in f if line.strip()]
            if not roasts:
                await interaction.followup.send("No roasts found 😅")
                return
        except FileNotFoundError:
            await interaction.followup.send("The file `roasts.txt` was not found.")
            return

        for _ in range(5):
            roast_text = random.choice(roasts)
            retries = 0
            while retries <= max_retries:
                try:
                    allowed = discord.AllowedMentions(everyone=True, users=True, roles=True)
                    await interaction.followup.send(f"{roast_text} {self.user.mention}", allowed_mentions=allowed)
                    await asyncio.sleep(self.delay + random.uniform(0.1, 0.5))
                    break
                except discord.errors.HTTPException as e:
                    if e.status == 429:
                        retry_after = getattr(e, "retry_after", 1.5)
                        retry_after = min(retry_after, 5)
                        print(f"Rate limit hit, retrying after {retry_after:.2f}s (retry {retries + 1}/{max_retries})")
                        await asyncio.sleep(retry_after)
                        retries += 1
                    else:
                        raise e
            else:
                print("Failed to send roast after max retries, skipping.")


@bot.tree.command(name="roast", description="Send a random roast to a user via button.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(user="The user to roast")
async def roast(interaction: discord.Interaction, user: discord.User):
    view = RoastButton(user, delay=0.5)
    await interaction.response.send_message("Press the button to send roasts! (5 per click)", view=view, ephemeral=True)



def random_time_today():
    base_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    random_minutes = random.randint(0, 23 * 60 + 59)  # 0 bis 1439 Minuten
    random_time = base_date + timedelta(minutes=random_minutes)
    return random_time

def _load_font(size, bold=False):
    """Load a usable font without requiring arial on the host."""
    candidates = []
    if bold:
        candidates += [
            "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "C:\\Windows\\Fonts\\arialbd.ttf",
            "C:\\Windows\\Fonts\\seguiemj.ttf",
        ]
    candidates += [
        "arial.ttf", "Arial.ttf", "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\seguiemj.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_text(text, font, max_width, draw):
    words = (text or "").split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for w in words[1:]:
        test = cur + " " + w
        try:
            tw = draw.textlength(test, font=font)
        except Exception:
            tw = len(test) * 8
        if tw <= max_width:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


@bot.tree.command(name="spoof-message", description="Send a realistic fake message as image.")
@app_commands.describe(username="Name to display", message="Fake message to show", avatar_url="Avatar image URL")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def spoof_image(interaction: discord.Interaction, username: str, message: str, avatar_url: str = None):
    await interaction.response.defer(ephemeral=False)

    try:
        if not avatar_url:
            avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"

        try:
            response = requests.get(avatar_url, timeout=15)
            avatar = Image.open(BytesIO(response.content)).convert("RGBA")
        except Exception:
            avatar = Image.new("RGBA", (40, 40), (114, 137, 218, 255))

        avatar = avatar.resize((40, 40), Image.LANCZOS)
        mask = Image.new("L", avatar.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0) + avatar.size, fill=255)
        avatar.putalpha(mask)

        font_bold = _load_font(18, bold=True)
        font_regular = _load_font(16, bold=False)
        font_timestamp = _load_font(12, bold=False)

        # measure wrapped message for dynamic height
        tmp = Image.new("RGBA", (800, 100), "#36393F")
        tmp_draw = ImageDraw.Draw(tmp)
        lines = _wrap_text(message, font_regular, 700, tmp_draw)
        line_h = 22
        height = max(80, 50 + len(lines) * line_h + 16)
        width = 800

        img = Image.new("RGBA", (width, height), "#36393F")
        draw = ImageDraw.Draw(img)
        img.paste(avatar, (20, 20), avatar)

        now = random_time_today().strftime("Today at %I:%M %p").lstrip("0").replace(" 0", " ")
        draw.text((70, 18), username, font=font_bold, fill=(255, 255, 255))
        try:
            name_w = draw.textlength(username, font=font_bold)
        except Exception:
            name_w = len(username) * 10
        draw.text((70 + name_w + 10, 21), now, font=font_timestamp, fill=(153, 170, 181))

        y = 45
        for line in lines:
            draw.text((70, y), line, font=font_regular, fill=(220, 221, 222))
            y += line_h

        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        file = discord.File(fp=buffer, filename="spoof.png")
        await interaction.followup.send(file=file)
    except Exception as e:
        try:
            await interaction.followup.send(f"spoof failed: {e}", ephemeral=True)
        except Exception:
            pass

    try:
        await log_command_use(user=interaction.user, command_name="spoof-message", message=message)
    except Exception:
        pass



@bot.tree.command(name="blame", description="Blame somebody else for raiding, and get them banned!")
@app_commands.describe(user="📰 The user you want to blame..")
async def blame(interaction: discord.Interaction, user: discord.User):
    await interaction.response.send_message("Blaming... ✏️", ephemeral=True)
    await interaction.followup.send(f"{user.mention}, Your Raid Command has been Successfully Completed! ✅")
    await log_command_use(interaction.user, "blame")



@bot.tree.command(name="anon-dm", description="Anonymously DM someone with a message.")
@app_commands.describe(user="The user you want to DM", message="The message to send")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.user_install()
async def anon_dm(interaction: discord.Interaction, user: discord.User, message: str):
    # Discord 50007 = cannot DM (closed DMs / blocked bot / no mutual server)
    try:
        await user.send(message)
        await interaction.response.send_message("Message sent anonymously ✅", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Couldn't DM that user — their DMs are closed or they blocked the bot.",
            ephemeral=True,
        )
    except discord.HTTPException as e:
        code = getattr(e, "code", None)
        if code == 50007 or getattr(e, "status", None) == 400:
            await interaction.response.send_message(
                "❌ Couldn't DM that user — their DMs are closed or they blocked the bot.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"❌ Failed to send DM (Discord error `{code or e}`).",
                ephemeral=True,
            )
    except Exception as e:
        # interaction may already be responded; still try
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"❌ Failed to send DM: `{e}`", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"❌ Failed to send DM: `{e}`", ephemeral=True
                )
        except Exception:
            pass

    try:
        await log_command_use(
            user=interaction.user,
            command_name="anon-dm",
            channel=interaction.channel,
            message=message,
        )
    except Exception:
        pass


@bot.tree.command(name="flooduser", description="[💎] Flood a user's DMs with messages. (premium only!)")
@app_commands.describe(
    user="The user to DM spam",
    message="Message to spam",
    times="How many times to send (1-200)",
    delay="Delay between messages in seconds (0.05-5)",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.user_install()
async def flooduser(
    interaction: discord.Interaction,
    user: discord.User,
    message: str,
    times: int = 10,
    delay: float = 0.25,
):
    if not is_premium_user(interaction.user.id):
        await interaction.response.send_message(
            "💎 This command is only available for premium users.", ephemeral=True
        )
        return

    # clamp args so Discord / rate limits don't instantly kill the flood
    times = max(1, min(int(times), 200))
    delay = max(0.05, min(float(delay), 5.0))

    if user.bot:
        await interaction.response.send_message("❌ Can't DM flood bots.", ephemeral=True)
        return

    await interaction.response.send_message(
        f"💣 Flooding {user.mention} with **{times}** DMs (delay `{delay}s`)...",
        ephemeral=True,
    )

    try:
        await log_command_use(
            user=interaction.user,
            command_name="💎 flooduser",
            channel=interaction.channel,
            message=message,
        )
    except Exception:
        pass

    sent = 0
    failed = 0
    closed = False

    # open / ensure DM channel once up front
    try:
        dm = user.dm_channel
        if dm is None:
            dm = await user.create_dm()
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Could not open DM (user has DMs closed or blocked the bot).",
            ephemeral=True,
        )
        return
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to open DM: `{e}`", ephemeral=True)
        return

    for i in range(times):
        try:
            await dm.send(message)
            sent += 1
        except discord.Forbidden:
            closed = True
            failed += 1
            break
        except discord.HTTPException as e:
            # rate limited / transient — brief backoff then continue
            failed += 1
            status = getattr(e, "status", None)
            if status == 429:
                retry = 1.0
                try:
                    retry = float(e.response.headers.get("Retry-After", 1.0))
                except Exception:
                    pass
                await asyncio.sleep(min(retry + 0.1, 5.0))
                continue
            # other HTTP errors: small pause and keep going
            await asyncio.sleep(0.5)
            continue
        except Exception:
            failed += 1
            await asyncio.sleep(0.3)
            continue

        if delay > 0 and i < times - 1:
            await asyncio.sleep(delay)

    if closed:
        await interaction.followup.send(
            f"⚠️ Stopped early — user closed DMs / blocked bot.\n"
            f"Sent **{sent}** / {times} messages.",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            f"✅ DM flood finished.\n"
            f"Sent **{sent}** / {times} · failed **{failed}**",
            ephemeral=True,
        )



@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Block buttons/modals for blacklist + own server. Log every slash command."""
    try:
        itype = interaction.type
        ival = int(itype.value) if hasattr(itype, "value") else int(itype)
    except Exception:
        ival = 0

    # Application command (slash / user / message context)
    if ival == 2:
        # 2 in some versions is component; discord.InteractionType.application_command == 2 in API
        pass

    # discord.InteractionType:
    # 1 ping, 2 application_command, 3 component, 4 autocomplete, 5 modal_submit
    if ival in (3, 5):  # component, modal submit
        if await hard_block_interaction(interaction):
            return

    # Log + leaderboard for every application command invocation (only if allowed in server)
    if ival == 2:  # application_command
        try:
            allowed, _reason = await user_allowed(interaction.user.id)
            if not allowed:
                return
            cmd_name = "unknown"
            data = getattr(interaction, "data", None) or {}
            if isinstance(data, dict):
                cmd_name = data.get("name") or "unknown"
                # nested subcommands
                opts = data.get("options") or []
                if opts and isinstance(opts, list):
                    o0 = opts[0]
                    if isinstance(o0, dict) and o0.get("type") in (1, 2) and o0.get("name"):
                        cmd_name = f"{cmd_name} {o0.get('name')}"
            # fire and forget so we don't delay the command
            asyncio.create_task(
                log_command_use(
                    user=interaction.user,
                    command_name=cmd_name,
                    channel=interaction.channel,
                    message=None,
                )
            )
            try:
                update_leaderboard(interaction.user.id, cmd_name)
            except Exception as e:
                print(f"[leaderboard] update failed: {e}")
        except Exception as e:
            print(f"[log] auto log failed: {e}")


@bot.event
async def on_ready():
    try:
        export_leaderboard_public()
        print("[leaderboard] public export refreshed")
    except Exception as _lb_e:
        print(f"[leaderboard] export on ready failed: {_lb_e}")
    print(f"Logged in as {bot.user} ({bot.user.id})")
    g = bot.get_guild(REQUIRED_GUILD_ID) if REQUIRED_GUILD_ID else None
    if REQUIRED_GUILD_ID:
        if g is None:
            print(f"[gate] WARNING: bot is NOT in required guild {REQUIRED_GUILD_ID} — invite the bot to your server!")
        else:
            print(f"[gate] required guild OK: {g.name} ({g.id}) members_intent={bot.intents.members}")

    # list local commands so you can see blacklist ones are registered in code
    try:
        local_names = sorted({c.name for c in bot.tree.get_commands()})
        print(f"[sync] local commands ({len(local_names)}): {', '.join(local_names)}")
        for need in ("x-blacklist", "x-unblacklist", "x-blacklist-list"):
            print(f"[sync] has {need}: {need in local_names}")
    except Exception as e:
        print(f"[sync] list local failed: {e}")

    # 1) guild sync = instant in your server
    if REQUIRED_GUILD_ID:
        try:
            guild_obj = discord.Object(id=int(REQUIRED_GUILD_ID))
            # copy global commands into the guild tree then sync guild
            bot.tree.copy_global_to(guild=guild_obj)
            synced_guild = await bot.tree.sync(guild=guild_obj)
            print(f"[sync] guild {REQUIRED_GUILD_ID}: synced {len(synced_guild)} commands (instant)")
            print("[sync] guild cmd names:", ", ".join(sorted(c.name for c in synced_guild)))
        except Exception as e:
            print(f"[sync] guild sync failed: {e}")

    # 2) global sync = shows everywhere (can take up to ~1 hour on Discord side)
    try:
        synced = await bot.tree.sync()
        print(f"[sync] global: synced {len(synced)} commands")
    except Exception as e:
        print(f"[sync] global sync failed: {e}")


if __name__ == "__main__":
    token = token_management()
    if not token:
        print("No token found. Set TOKEN in config.json")
        raise SystemExit(1)
    print("Starting Havoc panel bot...")
    try:
        bot.run(token)
    except discord.LoginFailure:
        print("Invalid token — check config.json TOKEN")
        raise SystemExit(1)
    except Exception as e:
        print(f"Bot crashed: {e}")
        raise
